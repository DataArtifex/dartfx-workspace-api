"""FastAPI dependency injection providers and error/quota mapping."""

from collections.abc import Generator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dartfx.workspace.access import (
    AccessPolicy,
    QuotaExceededError,
    WorkspaceAccessDeniedError,
    WorkspacePathTraversalError,
)
from dartfx.workspace.core import Workspace
from dartfx.workspace.models import AccessTier, Action, Principal, WorkspaceConfig
from dartfx.workspace.spaces import Space
from workspace_api.auth import extract_raw_api_key, resolve_principal
from workspace_api.config import Settings
from workspace_api.db import Database, space_record_to_config, workspace_record_to_config
from workspace_api.jobs import JobManager
from workspace_api.orm import SpaceRecord, WorkspaceRecord


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db_instance(request: Request) -> Database:
    return request.app.state.db


def get_job_manager(request: Request) -> JobManager:
    return request.app.state.job_manager


def get_db_session(db: Database = Depends(get_db_instance)) -> Generator[Session, None, None]:
    with db.SessionLocal() as session:
        yield session


def get_current_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    api_key: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> Principal:
    raw = extract_raw_api_key(x_api_key=x_api_key, authorization=authorization, api_key_query=api_key)
    return resolve_principal(raw, session, settings)


def require_user_or_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.tier == AccessTier.ANONYMOUS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required (User or Admin API key).",
        )
    return principal


def require_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.tier != AccessTier.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required.",
        )
    return principal


@dataclass
class AuthorizedWorkspace:
    """Resolved and authorized workspace bundle passed into endpoint handlers."""

    record: WorkspaceRecord
    config: WorkspaceConfig
    workspace: Workspace
    space_record: SpaceRecord | None
    effective_quota_bytes: int | None


def find_workspace_record(session: Session, ws_id: str) -> WorkspaceRecord | None:
    rec = session.get(WorkspaceRecord, ws_id)
    if rec is None:
        rec = session.scalar(select(WorkspaceRecord).where(WorkspaceRecord.slug == ws_id))
    return rec


def find_space_record(session: Session, space_id: str) -> SpaceRecord | None:
    rec = session.get(SpaceRecord, space_id)
    if rec is None:
        rec = session.scalar(select(SpaceRecord).where(SpaceRecord.slug == space_id))
    return rec


def compute_effective_quota_bytes(
    session: Session,
    ws_record: WorkspaceRecord,
    ws: Workspace,
    space_record: SpaceRecord | None,
) -> int | None:
    """Compute effective byte ceiling for a workspace considering both workspace and space quotas."""
    ws_quota = ws_record.storage_quota_bytes
    if space_record is None or space_record.storage_quota_bytes is None:
        return ws_quota

    space_cfg = space_record_to_config(space_record)
    space_obj = Space(space_cfg)
    sibling_records = session.scalars(select(WorkspaceRecord).where(WorkspaceRecord.space_id == space_record.id)).all()
    other_used = 0
    for sib in sibling_records:
        if sib.id == ws_record.id:
            continue
        other_used += Workspace(workspace_record_to_config(sib).path).total_usage_bytes()

    remaining_in_space = max(0, (space_obj.config.storage_quota_bytes or 0) - other_used)
    ws_used = ws.total_usage_bytes()
    space_ceiling_for_ws = remaining_in_space
    if ws_quota is None:
        return max(ws_used, space_ceiling_for_ws)
    return min(ws_quota, max(ws_used, space_ceiling_for_ws))


def resolve_authorized_workspace(
    ws_id: str,
    action: Action,
    session: Session,
    principal: Principal,
) -> AuthorizedWorkspace:
    """Resolve workspace by ID or slug, enforce AccessPolicy, and compute storage quota ceiling."""
    rec = find_workspace_record(session, ws_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workspace '{ws_id}' not found.")

    ws_cfg = workspace_record_to_config(rec)
    try:
        AccessPolicy.authorize(principal, ws_cfg, action)
    except WorkspaceAccessDeniedError as exc:
        code = status.HTTP_401_UNAUTHORIZED if principal.tier == AccessTier.ANONYMOUS else status.HTTP_403_FORBIDDEN
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    ws = Workspace(ws_cfg.path)
    space_rec = session.get(SpaceRecord, rec.space_id) if rec.space_id else None
    effective_quota = compute_effective_quota_bytes(session, rec, ws, space_rec)
    return AuthorizedWorkspace(
        record=rec,
        config=ws_cfg,
        workspace=ws,
        space_record=space_rec,
        effective_quota_bytes=effective_quota,
    )


def map_domain_error(exc: Exception) -> HTTPException:
    """Translate dartfx-workspace domain exceptions into semantic HTTP responses."""
    if isinstance(exc, WorkspacePathTraversalError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, QuotaExceededError):
        return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc))
    if isinstance(exc, WorkspaceAccessDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (ValueError, IsADirectoryError, RuntimeError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
