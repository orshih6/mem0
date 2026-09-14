# Self-host build (orshih6 fork)

Upstream publishes no maintained image for the current `server/` (the Docker Hub
`mem0/mem0-api-server:latest` predates the auth, dashboard and alembic work). This fork
adds only new files, so syncing upstream never conflicts:

| File | Purpose |
|---|---|
| `server/selfhost.Dockerfile` (+ `.dockerignore`) | Production API image: SDK from the same commit, no `--reload`, non-root, runs migrations on start |
| `.github/workflows/selfhost-images.yml` | Builds `ghcr.io/orshih6/mem0-server` and `ghcr.io/orshih6/mem0-dashboard` |

The dashboard image is upstream's `server/dashboard/Dockerfile` unchanged.

Update to a newer upstream:

```bash
gh repo sync orshih6/mem0 --branch main      # or: Sync fork in the GitHub UI
gh workflow run selfhost-images.yml -R orshih6/mem0 --ref main
```

Deployed to the k0s cluster from `~/myinternal/k0s/mem0/`.
