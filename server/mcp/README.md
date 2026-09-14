# mem0 MCP server (self-hosted)

An MCP server for the self-hosted REST API in `server/`. mem0's own MCP options
(`mcp.mem0.ai`, `mem0-mcp-server`, the editor plugins) only work with the hosted Platform.

- Transport: streamable HTTP at `/mcp`, stateless, JSON responses.
- Auth: no stored credentials. Clients send their own mem0 credential on every request
  (`Authorization: Bearer <m0sk_... key>` or `X-API-Key`), and it is forwarded to the API.
- Optional `X-Mem0-User-Id` header sets the default `user_id`.

Tools: `add_memory`, `search_memories`, `list_memories`, `get_memory`, `update_memory`,
`memory_history`, `delete_memory`, `delete_all_memories` (admin), `list_entities`.

```bash
pip install -r requirements.txt
MEM0_API_URL=http://localhost:8888 uvicorn server:app --port 8080
claude mcp add --transport http mem0 http://localhost:8080/mcp \
  --header "Authorization: Bearer m0sk_..." --header "X-Mem0-User-Id: me"
```

Image: `ghcr.io/orshih6/mem0-mcp`, built by `.github/workflows/selfhost-images.yml`.
