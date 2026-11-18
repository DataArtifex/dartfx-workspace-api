# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0]

- Enforced provenance and command eligibility guardrails in `/fairify` (only user-provided CSV/Parquet) and `/readstat` (only statistical files `dta|por|sas7bdat|sav|xpt|zsav`).
- Enhanced Web Client file explorer with Origin badge (`User` vs `System`), origin filtering, dynamic action button rendering based on `applicable_actions`, and detailed file provenance metadata in the Resource Inspector drawer.
- Initial release of `dartfx-workspace-api` compliant with `dartfx-foo-api` template specifications.
- Added SQLAlchemy 2.0 persistence supporting SQLite and PostgreSQL (`spaces`, `workspaces`, `api_keys`, `jobs`).
- Added 3-tier access control (Anonymous Ephemeral Sandbox, User/Team, Admin) backed by `dartfx.workspace.AccessPolicy`.
- Added REST endpoints for workspaces, spaces, files (browse, tree, preview, upload, download, ZIP archive, mkdir/mv/cp/rm), KB resources & RDF export, SPARQL & tabular SQL queries, and admin operations.
- Added multi-protocol async job manager (REST `202` polling, SSE `/jobs/{id}/events`, WebSocket `/ws/jobs`, HTTP webhooks, and AsyncAPI 3.0 `/asyncapi.json`).
- Added Canonical HTML Web Client served at `/ui` with User and Admin views.
