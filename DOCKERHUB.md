# dartfx-workspace-api

FastAPI service and canonical HTML web client for **Data Artifex Workspaces** (`dartfx-workspace`).

## Quick Start

```bash
docker run -d \
  --name dartfx-workspace-api \
  -p 127.0.0.1:8000:8000 \
  -e DATABASE_URL=sqlite:////app/cache/registry.db \
  -e DARTFX_ADMIN_API_KEY=change-me-admin-key \
  -v $(pwd)/cache:/app/cache \
  dartfx/dartfx-workspace-api:latest
```

## Endpoints
- `/` — Prefix-aware landing page
- `/ui` — Canonical HTML Web Client (Anonymous Sandbox, User/Team, Admin)
- `/docs` — Swagger / OpenAPI 3.1 UI
- `/asyncapi.json` — AsyncAPI 3.0 specification for WebSocket & SSE events
- `/status` & `/healthz` — Health & build timestamp observability
