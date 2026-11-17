"""FastAPI application entrypoint, gateway middleware, lifespan, and static UI delivery."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from workspace_api.__about__ import __version__
from workspace_api.asyncapi import get_asyncapi_schema
from workspace_api.config import Settings, get_settings
from workspace_api.db import Database
from workspace_api.jobs import JobManager
from workspace_api.routers import api_router

load_dotenv()

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ForwardedPrefixMiddleware:
    """Honors X-Forwarded-Prefix from Kong and Nginx to dynamically set ASGI root_path."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            forwarded_prefix = headers.get(b"x-forwarded-prefix", b"").decode("latin1").strip()
            if forwarded_prefix:
                prefix = forwarded_prefix.split(",")[0].strip().rstrip("/")
                if prefix and not prefix.startswith("/"):
                    prefix = f"/{prefix}"
                scope["root_path"] = prefix
        await self.app(scope, receive, send)


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    """Application factory creating a configured FastAPI service instance."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = custom_settings or get_settings()
        db = Database(settings)
        db.init_db()
        job_manager = JobManager(db)

        application.state.settings = settings
        application.state.db = db
        application.state.job_manager = job_manager

        try:
            yield
        finally:
            await job_manager.shutdown()
            db.dispose()

    application = FastAPI(
        title="Data Artifex Workspace API",
        version=__version__,
        description=(
            "FastAPI microservice exposing FAIR workspace management, file upload/download, "
            "RDF Knowledge Base metadata, SPARQL/SQL querying, and multi-protocol async jobs."
        ),
        lifespan=lifespan,
    )

    application.add_middleware(ForwardedPrefixMiddleware)

    cors_origins_env = os.getenv("CORS_ORIGINS", "*").strip()
    if cors_origins_env:
        allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
        application.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(api_router)

    if STATIC_DIR.is_dir():
        application.mount("/ui/static", StaticFiles(directory=str(STATIC_DIR)), name="ui-static")

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def root_landing(request: Request) -> HTMLResponse:
        root_path = str(request.scope.get("root_path", "")).rstrip("/")
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Data Artifex Workspace API</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      margin: 0;
      padding: 3rem 1.5rem;
      display: flex;
      justify-content: center;
    }}
    .card {{
      max-width: 720px;
      width: 100%;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 2rem;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    }}
    h1 {{ margin-top: 0; color: #38bdf8; }}
    p {{ color: #cbd5e1; line-height: 1.6; }}
    .links {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.75rem;
      margin-top: 1.5rem;
    }}
    a {{
      display: block;
      text-decoration: none;
      background: #0f172a;
      color: #f8fafc;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 0.85rem 1rem;
      font-weight: 600;
      transition: border-color 0.15s, transform 0.15s;
    }}
    a:hover {{ border-color: #38bdf8; transform: translateY(-1px); }}
    .primary {{ background: #0284c7; border-color: #38bdf8; }}
    small {{ display: block; font-weight: 400; color: #94a3b8; margin-top: 0.25rem; }}
  </style>
</head>
<body>
  <main class="card">
    <h1>Data Artifex Workspace API <span style="font-size:0.6em;color:#94a3b8;">v{__version__}</span></h1>
    <p>
      FAIR workspace management, file ingestion &amp; export, RDF Knowledge Base discovery,
      SPARQL/SQL querying, and asynchronous pipeline orchestration.
    </p>
    <div class="links">
      <a class="primary" href="{root_path}/ui">Canonical Web Client<small>{root_path}/ui</small></a>
      <a href="{root_path}/docs">Swagger / OpenAPI UI<small>{root_path}/docs</small></a>
      <a href="{root_path}/redoc">ReDoc Reference<small>{root_path}/redoc</small></a>
      <a href="{root_path}/openapi.json">OpenAPI 3.1 JSON<small>{root_path}/openapi.json</small></a>
      <a href="{root_path}/asyncapi.json">AsyncAPI 3.0 Spec<small>{root_path}/asyncapi.json</small></a>
      <a href="{root_path}/status">Service Status<small>{root_path}/status</small></a>
    </div>
  </main>
</body>
</html>"""
        return HTMLResponse(content=html)

    @application.get("/asyncapi.json", tags=["docs"], summary="AsyncAPI 3.0 specification for WebSocket & SSE channels")
    def asyncapi_spec(request: Request) -> JSONResponse:
        root_path = str(request.scope.get("root_path", ""))
        return JSONResponse(content=get_asyncapi_schema(server_prefix=root_path))

    @application.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    @application.get("/ui/", response_class=HTMLResponse, include_in_schema=False)
    def web_client(request: Request) -> HTMLResponse:
        root_path = str(request.scope.get("root_path", "")).rstrip("/")
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h1>Web client assets missing.</h1>", status_code=404)
        content = index_file.read_text(encoding="utf-8")
        content = content.replace("__ROOT_PATH__", root_path)
        return HTMLResponse(content=content)

    return application


app = create_app()


def get_static_context(data: dict[str, Any] | None = None) -> dict[str, Any]:
    return data or {}
