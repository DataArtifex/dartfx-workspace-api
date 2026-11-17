"""Aggregate API routers for dartfx-workspace-api."""

from fastapi import APIRouter

from workspace_api.routers import (
    admin,
    auth_info,
    files,
    health,
    jobs,
    queries,
    resources,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth_info.router)
api_router.include_router(workspaces.router)
api_router.include_router(files.router)
api_router.include_router(resources.router)
api_router.include_router(queries.router)
api_router.include_router(jobs.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
