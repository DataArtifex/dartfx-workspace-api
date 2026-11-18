# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0]

- Enhanced Web Client file explorer (`app.js`, `styles.css`) to group system-generated FAIRification/ReadStat sidecars under their parent dataset (via `source_uuid` with filename fallback) using indented, muted child rows and a collapsible artifact count pill (`▸ N` / `▾ N`).
- Added `GET /workspaces/{ws_id}/settings` and `PATCH /workspaces/{ws_id}/settings` (Admin) to inspect and configure workspace rename and content-zoning governance policies (`WorkspaceSettings`).
- Enforced workspace `same_format` rename policies and path-based zone upload restrictions (`data/**`, `docs/**`, `code/**`, `meta/**`, `work/**`) across `/files/upload` and `/files/move`, returning `400 Bad Request` on policy violations.
- Added cascading sidecar rename support to `POST /workspaces/{ws_id}/files/move`: moving or renaming a dataset automatically moves and renames all related FAIRification sidecars on disk and in the Knowledge Base, preserving each artifact's stable UUID.
- Enriched Web Client Resource Inspector drawer with `Source Dataset` UUID provenance for system-generated artifacts.
- Added cascading artifact cleanup on file deletion in `DELETE /workspaces/{ws_id}/files` with `delete_artifacts` parameter (defaults to `true`), returning deleted sidecar paths in `deleted_artifacts`.
- Enhanced Web Client file deletion with confirmation context and toast reporting when related FAIRification artifacts are removed alongside a dataset.

- Added FAIRify freshness detection (`fairify_status`: `available`, `up_to_date`, `stale`) and blocked redundant execution on up-to-date files unless `force=true` is passed; enhanced Web Client with `FAIRified ✔` and `FAIRify Outdated ⚠️` badges and drawer force re-run option.
- Enforced provenance and command eligibility guardrails in `/fairify` (only user-provided CSV/Parquet) and `/readstat` (only statistical files `dta|por|sas7bdat|sav|xpt|zsav`).
- Enhanced Web Client file explorer with Origin badge (`User` vs `System`), origin filtering, dynamic action button rendering based on `applicable_actions`, and detailed file provenance metadata in the Resource Inspector drawer.
- Initial release of `dartfx-workspace-api` compliant with `dartfx-foo-api` template specifications.
- Added SQLAlchemy 2.0 persistence supporting SQLite and PostgreSQL (`spaces`, `workspaces`, `api_keys`, `jobs`).
- Added 3-tier access control (Anonymous Ephemeral Sandbox, User/Team, Admin) backed by `dartfx.workspace.AccessPolicy`.
- Added REST endpoints for workspaces, spaces, files (browse, tree, preview, upload, download, ZIP archive, mkdir/mv/cp/rm), KB resources & RDF export, SPARQL & tabular SQL queries, and admin operations.
- Added multi-protocol async job manager (REST `202` polling, SSE `/jobs/{id}/events`, WebSocket `/ws/jobs`, HTTP webhooks, and AsyncAPI 3.0 `/asyncapi.json`).
- Added Canonical HTML Web Client served at `/ui` with User and Admin views.
