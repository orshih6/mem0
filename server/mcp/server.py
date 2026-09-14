"""MCP server for a self-hosted mem0 REST API (orshih6 fork, not upstream).

mem0's own MCP offerings (mcp.mem0.ai, mem0-mcp-server, the editor plugins) only speak
the hosted Platform API. This wraps the self-hosted FastAPI server in `server/` instead.

Transport: streamable HTTP at MCP_PATH (default /mcp), stateless with JSON responses,
so it needs no sticky sessions or long-lived streams behind a reverse proxy.

Auth: the server stores no credentials. Each request must carry the caller's own mem0
credential, which is forwarded unchanged to the mem0 API:
  Authorization: Bearer <m0sk_... API key | ADMIN_API_KEY | dashboard JWT>
  X-API-Key: <key>
Optional header X-Mem0-User-Id sets the default user_id for calls that omit a scope.

Env:
  MEM0_API_URL           mem0 REST API base URL     (default http://localhost:8888)
  MEM0_DEFAULT_USER_ID   fallback user_id           (default: none, scope required)
  MEM0_REQUEST_TIMEOUT   seconds per API call       (default 180; adds call an LLM)
  MCP_PATH               MCP endpoint path          (default /mcp)
"""

import json
import logging
import os
import urllib.parse
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

API_URL = os.environ.get("MEM0_API_URL", "http://localhost:8888").rstrip("/")
DEFAULT_USER_ID = os.environ.get("MEM0_DEFAULT_USER_ID", "").strip()
TIMEOUT = float(os.environ.get("MEM0_REQUEST_TIMEOUT", "180"))
MCP_PATH = "/" + os.environ.get("MCP_PATH", "/mcp").strip("/")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mem0-mcp")


class Mem0Error(ToolError):
    """Raised inside a tool. Only ToolError messages reach the client; other exceptions become a generic error."""


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=10.0))
    return _client


def _credential_headers(ctx: Context) -> dict[str, str]:
    headers = ctx.headers or {}
    key = (headers.get("x-api-key") or "").strip()
    if not key:
        auth = headers.get("authorization") or ""
        if auth[:7].lower() == "bearer ":
            key = auth[7:].strip()
    if not key:
        raise Mem0Error("No mem0 credential. Send 'Authorization: Bearer <mem0 API key>' or 'X-API-Key: <key>'.")
    # Dashboard JWTs are Bearer tokens to the API; everything else (m0sk_ keys, ADMIN_API_KEY) is X-API-Key.
    if key.startswith("eyJ") and key.count(".") == 2:
        return {"Authorization": f"Bearer {key}"}
    return {"X-API-Key": key}


def _scope(ctx: Context, user_id: str | None, agent_id: str | None, run_id: str | None, *, allow_default: bool = True) -> dict[str, str]:
    scope = {k: v for k, v in {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}.items() if v}
    if not scope and allow_default:
        default = ((ctx.headers or {}).get("x-mem0-user-id") or DEFAULT_USER_ID).strip()
        if default:
            scope["user_id"] = default
    if not scope:
        raise Mem0Error(
            "Pass at least one of user_id, agent_id or run_id"
            + (" (or set a default user with the X-Mem0-User-Id header)." if allow_default else ".")
        )
    return scope


async def _call(ctx: Context, method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
    try:
        resp = await _http().request(method, API_URL + path, params=params, json=body, headers=_credential_headers(ctx))
    except httpx.HTTPError as exc:
        log.warning("mem0 API %s %s failed: %s", method, path, exc)
        raise Mem0Error(f"mem0 API unreachable ({type(exc).__name__}).") from exc
    if resp.status_code >= 400:
        try:
            detail: Any = resp.json().get("detail") or resp.text
        except ValueError:
            detail = resp.text
        if not isinstance(detail, str):
            detail = json.dumps(detail)
        raise Mem0Error(f"mem0 API returned {resp.status_code}: {detail[:500]}")
    return resp.json() if resp.content else {}


def _as_dict(result: Any, key: str = "result", *, not_found: str | None = None) -> dict[str, Any]:
    """Tools declare dict output; the API sometimes answers null (unknown id) or a bare list."""
    if result is None:
        raise Mem0Error(not_found or "mem0 API returned an empty response.")
    return result if isinstance(result, dict) else {key: result}


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


UserId = Annotated[str | None, Field(description="End user the memories belong to. Defaults to the X-Mem0-User-Id header, if set.")]
AgentId = Annotated[str | None, Field(description="Agent the memories belong to.")]
RunId = Annotated[str | None, Field(description="Session or run the memories belong to.")]

mcp = MCPServer(
    name="mem0",
    title="mem0 (self-hosted)",
    instructions=(
        "Long-term memory backed by a self-hosted mem0 server. Search before answering questions that may depend "
        "on what the user told you earlier, and add a memory when the user shares a durable preference, fact or "
        "decision. Scope every call with user_id, agent_id or run_id unless a default user is configured."
    ),
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool(
    description=(
        "Store memories. mem0 extracts durable facts from the text with an LLM (set infer=false to store it "
        "verbatim) and merges them with what it already knows. Pass `content` for a single note, or `messages` "
        "for a conversation excerpt."
    ),
    annotations=ToolAnnotations(title="Add memory", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def add_memory(
    ctx: Context,
    content: Annotated[str | None, Field(description="Text to remember, stored as a single user message.")] = None,
    messages: Annotated[list[Message] | None, Field(description="Conversation excerpt to extract memories from.")] = None,
    user_id: UserId = None,
    agent_id: AgentId = None,
    run_id: RunId = None,
    metadata: Annotated[dict[str, Any] | None, Field(description="Arbitrary key/value metadata to attach.")] = None,
    infer: Annotated[bool, Field(description="Extract facts with the LLM (true) or store the text as-is (false).")] = True,
) -> dict[str, Any]:
    if messages:
        msgs = [m.model_dump() for m in messages]
    elif content and content.strip():
        msgs = [{"role": "user", "content": content}]
    else:
        raise Mem0Error("Provide `content` or `messages`.")
    body: dict[str, Any] = {"messages": msgs, "infer": infer, **_scope(ctx, user_id, agent_id, run_id)}
    if metadata:
        body["metadata"] = metadata
    return _as_dict(await _call(ctx, "POST", "/memories", body=body), "results")


@mcp.tool(
    description="Semantic search over stored memories, most relevant first.",
    annotations=ToolAnnotations(title="Search memories", readOnlyHint=True, openWorldHint=False),
)
async def search_memories(
    ctx: Context,
    query: Annotated[str, Field(description="What to look for, in natural language.")],
    user_id: UserId = None,
    agent_id: AgentId = None,
    run_id: RunId = None,
    top_k: Annotated[int | None, Field(ge=1, le=100, description="Maximum results.")] = 10,
    threshold: Annotated[float | None, Field(ge=0, le=1, description="Minimum similarity score.")] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "filters": _scope(ctx, user_id, agent_id, run_id)}
    if top_k is not None:
        body["top_k"] = top_k
    if threshold is not None:
        body["threshold"] = threshold
    return _as_dict(await _call(ctx, "POST", "/search", body=body), "results")


@mcp.tool(
    description="List stored memories for a user, agent or run (no ranking).",
    annotations=ToolAnnotations(title="List memories", readOnlyHint=True, openWorldHint=False),
)
async def list_memories(
    ctx: Context,
    user_id: UserId = None,
    agent_id: AgentId = None,
    run_id: RunId = None,
    top_k: Annotated[int | None, Field(ge=1, le=1000, description="Maximum memories to return.")] = 100,
) -> dict[str, Any]:
    params: dict[str, Any] = dict(_scope(ctx, user_id, agent_id, run_id))
    if top_k is not None:
        params["top_k"] = top_k
    return _as_dict(await _call(ctx, "GET", "/memories", params=params), "results")


@mcp.tool(
    description="Fetch one memory by id.",
    annotations=ToolAnnotations(title="Get memory", readOnlyHint=True, openWorldHint=False),
)
async def get_memory(ctx: Context, memory_id: Annotated[str, Field(description="Memory id.")]) -> dict[str, Any]:
    return _as_dict(await _call(ctx, "GET", f"/memories/{_quote(memory_id)}"), not_found=f"Memory {memory_id} not found.")


@mcp.tool(
    description="Replace a memory's text and/or metadata.",
    annotations=ToolAnnotations(title="Update memory", readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False),
)
async def update_memory(
    ctx: Context,
    memory_id: Annotated[str, Field(description="Memory id.")],
    text: Annotated[str | None, Field(description="New memory text.")] = None,
    metadata: Annotated[dict[str, Any] | None, Field(description="New metadata.")] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if text is not None:
        body["text"] = text
    if metadata is not None:
        body["metadata"] = metadata
    if not body:
        raise Mem0Error("Provide `text` and/or `metadata`.")
    return _as_dict(await _call(ctx, "PUT", f"/memories/{_quote(memory_id)}", body=body), not_found=f"Memory {memory_id} not found.")


@mcp.tool(
    description="Show how a memory changed over time (adds, updates, deletes).",
    annotations=ToolAnnotations(title="Memory history", readOnlyHint=True, openWorldHint=False),
)
async def memory_history(ctx: Context, memory_id: Annotated[str, Field(description="Memory id.")]) -> dict[str, Any]:
    return _as_dict(await _call(ctx, "GET", f"/memories/{_quote(memory_id)}/history"), "history")


@mcp.tool(
    description="Delete one memory by id.",
    annotations=ToolAnnotations(title="Delete memory", readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False),
)
async def delete_memory(ctx: Context, memory_id: Annotated[str, Field(description="Memory id.")]) -> dict[str, Any]:
    return _as_dict(await _call(ctx, "DELETE", f"/memories/{_quote(memory_id)}"))


@mcp.tool(
    description=(
        "Delete ALL memories for the given user, agent or run. Requires an admin credential. The scope must be "
        "passed explicitly; the default user is never applied here."
    ),
    annotations=ToolAnnotations(title="Delete all memories", readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False),
)
async def delete_all_memories(ctx: Context, user_id: UserId = None, agent_id: AgentId = None, run_id: RunId = None) -> dict[str, Any]:
    return _as_dict(await _call(ctx, "DELETE", "/memories", params=_scope(ctx, user_id, agent_id, run_id, allow_default=False)))


@mcp.tool(
    description="List the users, agents and runs that currently have memories.",
    annotations=ToolAnnotations(title="List entities", readOnlyHint=True, openWorldHint=False),
)
async def list_entities(ctx: Context) -> dict[str, Any]:
    return _as_dict(await _call(ctx, "GET", "/entities"), "entities")


class RequireCredential:
    """Reject MCP requests that carry no credential with a plain 401 before the MCP layer runs."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") == MCP_PATH and scope["method"] != "OPTIONS":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            if not headers.get("x-api-key") and headers.get("authorization", "")[:7].lower() != "bearer ":
                response = JSONResponse(
                    {"error": "mem0 credential required: send 'Authorization: Bearer <mem0 API key>' or 'X-API-Key'."},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = RequireCredential(
    mcp.streamable_http_app(streamable_http_path=MCP_PATH, stateless_http=True, json_response=True, host="0.0.0.0")
)
