# dartfx-workspace-api

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com/)
[![CI](https://github.com/DataArtifex/dartfx-workspace-api/actions/workflows/test.yml/badge.svg)](https://github.com/DataArtifex/dartfx-workspace-api/actions/workflows/test.yml)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg?logo=ruff&logoColor=D7FF64)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.txt)

A FastAPI microservice and canonical HTML web client for **Data Artifex Workspaces** (`dartfx-workspace`), following the canonical `dartfx-foo-api` architecture.

## Key Capabilities

- **Dual Registry Persistence**: Powered by SQLAlchemy 2.0 supporting **SQLite** (testing & lightweight setups) and **PostgreSQL** (`postgresql+psycopg://...` for production).
- **3-Tier Access Control**:
  - **Anonymous / Public**: Zero-auth access to an auto-provisioned, quota-capped Public Ephemeral Sandbox workspace.
  - **User / Team**: `X-API-Key` or Bearer token unlocking private and team-shared Spaces/Workspaces.
  - **Admin**: Full access to register host workspaces, manage Spaces & storage quotas, issue/revoke API keys, reset the ephemeral sandbox, and monitor system tools.
- **Complete File & Metadata Lifecycle**:
  - Browse (`ls`, `tree`), inspect (`about`, `head`/`tail`), **upload** single/multiple files with quota checks and automatic Knowledge Base registration, **download** single files by path or UUID, and export directories/workspaces as **ZIP archives**.
  - Export FAIR metadata in **Turtle**, **JSON-LD**, **RDF/XML**, and **N3**.
- **Multi-Protocol Asynchronous Execution**:
  - Background `scan`, `fairify` (`qsv`), `readstat`, and `tocsv` operations exposed via **REST `202` polling** (`/jobs/{id}`), **Server-Sent Events** (`/jobs/{id}/events`), **WebSockets** (`/ws/jobs`), **HTTP Webhook Callbacks**, and an auto-generated **AsyncAPI 3.0** specification (`/asyncapi.json`).
- **Graph & Analytical Querying**:
  - Read-only **SPARQL** against the RDF Knowledge Base (`/workspaces/{ws_id}/sparql`) and read-only **SQL** over workspace tabular datasets (`/workspaces/{ws_id}/query`).
- **Canonical HTML Web Client (`/ui`)**:
  - Prefix-aware single-page portal for anonymous explorers, workspace curators, and administrators.

## Local Development

### Monorepo Environment (Recommended)

When working inside the Data Artifex monorepo, run using the shared root virtual environment with `--no-sync` (which avoids re-syncing the monorepo root environment and avoids cloud-storage lock delays):

```bash
PYTHONPATH=src/dartfx uv run --no-sync uvicorn workspace_api.main:app --reload --port 8000
```

### Standalone Environment

When working outside the monorepo in an isolated repository:

```bash
uv sync --group dev
PYTHONPATH=src/dartfx uv run uvicorn workspace_api.main:app --reload --port 8000
```

Open:
- Web Client: `http://127.0.0.1:8000/ui`
- OpenAPI Swagger UI: `http://127.0.0.1:8000/docs`
- AsyncAPI 3.0 Spec: `http://127.0.0.1:8000/asyncapi.json`
- Health Status: `http://127.0.0.1:8000/status`

## Docker Compose Deployment

```bash
cp .env.example .env
# Optionally customize PostgreSQL, quotas, or Kong networks:
# cp docker-compose.override.example.yaml docker-compose.override.yaml
docker compose up -d --build
```

### Reverse Proxy / API Gateway (Kong & Nginx)

The service mounts `ForwardedPrefixMiddleware` to honor `X-Forwarded-Prefix` automatically (e.g., Kong routes with `strip_path: true` or Nginx `proxy_set_header X-Forwarded-Prefix /workspace-api;`). All links on `/`, `/docs`, `/redoc`, and `/ui` adapt dynamically to the forwarded prefix.

## Testing & Verification

```bash
python3 scripts/audit_api.py
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check src tests
uv run pytest
uv run sphinx-build -b html docs/source docs/build/html
```
