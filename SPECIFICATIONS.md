# SPECIFICATIONS: `dartfx-workspace-api`

`dartfx-workspace-api` exposes the programmatic domain capabilities of `dartfx-workspace` as a containerized FastAPI microservice with a canonical HTML web portal (`/ui`).

## Core Requirements
1. **100% `dartfx-foo-api` Template Compliance**:
   - Hardened non-root Dockerfile (`dartfx/docker-api-base:latest`), `docker-compose.yaml`, `ForwardedPrefixMiddleware`, dynamic `CORS_ORIGINS`, `/status` and `/healthz` probes.
2. **Dual Persistence Engine**:
   - SQLAlchemy 2.0 ORM supporting SQLite (`sqlite:///...`) and PostgreSQL (`postgresql+psycopg://...`).
3. **Three-Tier Multi-Tenant Access**:
   - Anonymous public access to a quota-limited ephemeral sandbox workspace (`visibility="public"`).
   - User/Team API keys for private and team workspaces (`visibility="private" | "team"`).
   - Admin API keys for space/workspace lifecycle, API key provisioning, KB wipes, and system diagnostics.
4. **Full File & FAIR Metadata Lifecycle**:
   - Directory listing, tree traversal, `head`/`tail` inspection, single/multi-file upload with streaming quota checks and KB auto-indexing, single-file and ZIP archive download, and RDF serialization (`turtle`, `json-ld`, `xml`, `n3`).
5. **Multi-Protocol Async Jobs**:
   - Background execution for `scan`, `fairify`, `readstat`, and `tocsv` with REST `202` polling, SSE streams, WebSocket channel, Webhook callbacks, and AsyncAPI 3.0 schema.
6. **Canonical HTML Web Client (`/ui`)**:
   - Gateway-prefix-aware web UI covering anonymous exploration, curation, upload/download, SPARQL/SQL querying, live SSE job monitoring, and admin operations.
