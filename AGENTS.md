# AI Agent Instructions

Welcome, fellow AI. This file provides context and instructions for working on `dartfx-workspace-api`.

## Project Specifications

See @SPECIFICATIONS.md and @IMPLEMENTATION.md

## Project Stack

- **Language**: Python 3.12+ (Strictly required)
- **Framework**: FastAPI (`>=0.135.1`), Uvicorn (`>=0.41.0`), Gunicorn (`>=23.0.0`), Pydantic v2
- **Domain Core**: `dartfx-workspace`
- **Persistence**: SQLAlchemy 2.0 supporting SQLite (testing/lightweight) and PostgreSQL via `psycopg[binary]`
- **Package & Workflow**: `uv` and `hatch`
- **Linting & Formatting**: `ruff` (`line-length = 120`, `target-version = "py312"`)
- **Type Checking**: `pyrefly` (`uv run pyrefly check src tests`)
- **Testing**: `pytest` + `coverage` + `httpx`
- **Documentation**: Sphinx + MyST-Parser + Read the Docs theme

## Rapid Commands

```bash
# Local dev server (monorepo)
PYTHONPATH=src/dartfx uv run --no-sync uvicorn workspace_api.main:app --reload --port 8000

# Docker Compose deployment
cp .env.example .env
docker compose up -d --build

# Quality checks
python3 scripts/audit_api.py
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check src tests
uv run pytest
uv run sphinx-build -b html docs/source docs/build/html
```
