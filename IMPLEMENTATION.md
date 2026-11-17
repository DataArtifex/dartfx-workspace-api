# Implementation Guide: `dartfx-workspace-api`

## Architecture
- **FastAPI Entrypoint (`workspace_api/main.py`)**: Configures ASGI lifespan (`init_db()`), `ForwardedPrefixMiddleware` for `X-Forwarded-Prefix` gateway routing, configurable `CORSMiddleware`, `/asyncapi.json`, `/ui` HTML delivery, and `/ui/static` assets.
- **Persistence (`workspace_api/orm.py`, `workspace_api/db.py`)**: SQLAlchemy 2.0 engine configured via `DATABASE_URL` with tables `spaces`, `workspaces`, `api_keys`, and `jobs`. On initial startup, provisions a default `public` Space and `sandbox` ephemeral Workspace via `dartfx.workspace.Space.provision_ephemeral_workspace`.
- **3-Tier Auth (`workspace_api/auth.py`, `workspace_api/dependencies.py`)**: Resolves `X-API-Key`, `Authorization: Bearer`, or `?api_key=` into a `dartfx.workspace.Principal` (`anonymous`, `user`, `admin`) and enforces `dartfx.workspace.AccessPolicy` alongside Space and Workspace storage quotas.
- **Async Job Manager (`workspace_api/jobs.py`, `workspace_api/asyncapi.py`)**: Runs `dartfx.workspace.tasks` in worker threads, persists `JobRecord` updates in the database, fans out `TaskEvent` frames to SSE and WebSocket `asyncio.Queue` subscribers, and dispatches optional HTTP POST webhooks.
- **Canonical Web Client (`workspace_api/static/`)**: Pure HTML/CSS/Vanilla JS SPA computing its API prefix dynamically from `window.location.pathname` so it operates transparently at `/` or behind reverse-proxy subpaths.
