# Contributing to dartfx-workspace-api

Thank you for your interest in contributing! By participating in this project, you agree to abide by our [Code of Conduct](./CODE_OF_CONDUCT.md).

## Maintainers
- **Pascal Heus** ([@kulnor](https://github.com/kulnor))

## Development Workflow

This project uses **uv** for dependency management and **Hatch** for project orchestration.

### 1. Environment Setup
```bash
uv sync
uv run pre-commit install
```

### 2. Quality Gates
- **Template Audit**: `python3 scripts/audit_api.py`
- **Lint & Format**: `uv run ruff check .` and `uv run ruff format --check .`
- **Type Check**: `uv run pyrefly check src tests`
- **Tests**: `uv run pytest`
- **Docs**: `uv run sphinx-build -b html docs/source docs/build/html`
