"""Administrative endpoints for Spaces, Workspaces, Ephemeral Sandbox Reset, and API Keys."""

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dartfx.workspace.core import Workspace
from dartfx.workspace.models import Principal
from dartfx.workspace.spaces import Space
from workspace_api.auth import generate_api_key, hash_api_key
from workspace_api.config import Settings
from workspace_api.db import (
    DEFAULT_PUBLIC_SPACE_ID,
    DEFAULT_SANDBOX_WORKSPACE_ID,
    space_record_to_config,
)
from workspace_api.dependencies import (
    find_space_record,
    find_workspace_record,
    get_db_session,
    get_settings_dep,
    require_admin,
)
from workspace_api.models import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    SpaceCreateRequest,
    SpaceResponse,
    WorkspaceCreateRequest,
    WorkspaceDetailResponse,
    WorkspaceInitRequest,
    WorkspaceResponse,
)
from workspace_api.orm import ApiKeyRecord, SpaceRecord, WorkspaceRecord

router = APIRouter(prefix="/admin", tags=["admin"])


def _api_key_record_to_info(rec: ApiKeyRecord) -> ApiKeyInfo:
    from dartfx.workspace.models import AccessTier

    return ApiKeyInfo(
        id=rec.id,
        key_prefix=rec.key_prefix,
        label=rec.label,
        tier=AccessTier(rec.tier),
        team_ids=json.loads(rec.team_ids_json) if rec.team_ids_json else [],
        allowed_space_ids=json.loads(rec.allowed_space_ids_json) if rec.allowed_space_ids_json else [],
        is_active=rec.is_active,
        created_at=rec.created_at,
        last_used_at=rec.last_used_at,
    )


@router.post("/spaces", status_code=status.HTTP_201_CREATED, response_model=SpaceResponse, summary="Create a Space")
def create_space(
    payload: SpaceCreateRequest,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> SpaceResponse:
    """Register or create a new virtual Space."""
    existing = session.scalar(select(SpaceRecord).where(SpaceRecord.slug == payload.slug))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Space slug '{payload.slug}' already exists.")

    space_id = payload.id or payload.slug
    if session.get(SpaceRecord, space_id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Space id '{space_id}' already exists.")

    root_path = (
        Path(payload.root_path).resolve() if payload.root_path else (settings.storage_root / payload.slug).resolve()
    )
    root_path.mkdir(parents=True, exist_ok=True)

    rec = SpaceRecord(
        id=space_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        root_path=str(root_path),
        storage_quota_bytes=payload.storage_quota_bytes,
        visibility=payload.visibility.value,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)

    stats = Space(space_record_to_config(rec)).stats([])
    return SpaceResponse(
        id=rec.id,
        slug=rec.slug,
        name=rec.name,
        description=rec.description,
        root_path=rec.root_path,
        storage_quota_bytes=rec.storage_quota_bytes,
        visibility=payload.visibility,
        created_at=rec.created_at,
        stats=stats,
    )


@router.delete("/spaces/{space_id}", summary="Unregister a Space")
def delete_space(
    space_id: str,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Delete a Space record from the registry (workspaces remain intact on disk)."""
    rec = find_space_record(session, space_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Space '{space_id}' not found.")
    if rec.id == DEFAULT_PUBLIC_SPACE_ID:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete default public space.")

    session.delete(rec)
    session.commit()
    return {"id": rec.id, "status": "deleted"}


@router.post(
    "/workspaces",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceResponse,
    summary="Create or register a Workspace",
)
def create_workspace(
    payload: WorkspaceCreateRequest,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> WorkspaceResponse:
    """Register an existing host workspace directory or provision a new workspace."""
    if session.scalar(select(WorkspaceRecord).where(WorkspaceRecord.slug == payload.slug)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workspace slug '{payload.slug}' already exists.",
        )

    ws_id = payload.id or payload.slug
    if session.get(WorkspaceRecord, ws_id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Workspace id '{ws_id}' already exists.")

    space_rec: SpaceRecord | None = None
    if payload.space_id:
        space_rec = find_space_record(session, payload.space_id)
        if space_rec is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Space '{payload.space_id}' not found.")

    if payload.path:
        ws_path = Path(payload.path).resolve()
    elif space_rec is not None:
        ws_path = (Path(space_rec.root_path) / payload.slug).resolve()
    else:
        ws_path = (settings.storage_root / payload.slug).resolve()

    ws_path.mkdir(parents=True, exist_ok=True)
    ws = Workspace(ws_path)
    if payload.initialize and not ws.is_initialized():
        ws = Workspace.init(ws_path, create_dirs=payload.create_dirs)
        if payload.create_dirs:
            (ws.path / "prod").mkdir(exist_ok=True)
        ws.scan()
    elif ws.is_initialized():
        ws.scan()

    rec = WorkspaceRecord(
        id=ws_id,
        space_id=space_rec.id if space_rec else None,
        slug=payload.slug,
        name=payload.name,
        path=str(ws_path),
        visibility=payload.visibility.value,
        is_ephemeral=payload.is_ephemeral,
        storage_quota_bytes=payload.storage_quota_bytes,
        owner_id=payload.owner_id,
        team_id=payload.team_id,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)

    return WorkspaceResponse(
        id=rec.id,
        space_id=rec.space_id,
        slug=rec.slug,
        name=rec.name,
        path=rec.path,
        visibility=payload.visibility,
        is_ephemeral=rec.is_ephemeral,
        storage_quota_bytes=rec.storage_quota_bytes,
        owner_id=rec.owner_id,
        team_id=rec.team_id,
        initialized=ws.is_initialized(),
        created_at=rec.created_at,
    )


@router.delete("/workspaces/{ws_id}", summary="Unregister or delete a Workspace")
def delete_workspace(
    ws_id: str,
    delete_files: bool = Query(default=False),
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Unregister a workspace from the registry and optionally delete files from disk."""
    rec = find_workspace_record(session, ws_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workspace '{ws_id}' not found.")
    if rec.id == DEFAULT_SANDBOX_WORKSPACE_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Default public sandbox cannot be deleted; use POST /admin/ephemeral/reset.",
        )

    ws_path = Path(rec.path)
    deleted_id = rec.id
    session.delete(rec)
    session.commit()

    if delete_files and ws_path.exists():
        shutil.rmtree(ws_path)

    return {"id": deleted_id, "status": "deleted"}


@router.post("/workspaces/{ws_id}/init", response_model=WorkspaceDetailResponse, summary="Initialize workspace .dartfx")
def init_workspace(
    ws_id: str,
    payload: WorkspaceInitRequest,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> WorkspaceDetailResponse:
    """Initialize .dartfx metadata store and optional standard FAIR directories in a workspace."""
    from dartfx.workspace.models import Visibility

    rec = find_workspace_record(session, ws_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workspace '{ws_id}' not found.")

    ws = Workspace.init(Path(rec.path), create_dirs=payload.create_dirs)
    if payload.create_dirs:
        (ws.path / "prod").mkdir(exist_ok=True)
    ws.scan()
    ws_stats = ws.stats()

    return WorkspaceDetailResponse(
        id=rec.id,
        space_id=rec.space_id,
        slug=rec.slug,
        name=rec.name,
        path=rec.path,
        visibility=Visibility(rec.visibility),
        is_ephemeral=rec.is_ephemeral,
        storage_quota_bytes=rec.storage_quota_bytes,
        owner_id=rec.owner_id,
        team_id=rec.team_id,
        initialized=True,
        created_at=rec.created_at,
        used_bytes=ws_stats.total_size_bytes,
        stats=ws_stats,
    )


@router.delete("/workspaces/{ws_id}/kb", summary="Wipe workspace RDF Knowledge Base")
def wipe_workspace_kb(
    ws_id: str,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Clear all RDF resource metadata in the workspace Knowledge Base."""
    rec = find_workspace_record(session, ws_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workspace '{ws_id}' not found.")
    ws = Workspace(Path(rec.path))
    if ws.is_initialized():
        ws.kb.wipe()
    return {"workspace_id": rec.id, "status": "kb_wiped"}


@router.post("/ephemeral/reset", response_model=WorkspaceDetailResponse, summary="Reset Public Ephemeral Sandbox")
def reset_ephemeral_sandbox(
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> WorkspaceDetailResponse:
    """Wipe and re-seed the default public ephemeral sandbox workspace."""
    from dartfx.workspace.models import Visibility

    rec = find_workspace_record(session, DEFAULT_SANDBOX_WORKSPACE_ID)
    if rec is None:
        sandbox_path = (settings.storage_root / "public" / "sandbox").resolve()
        ws = Space.reset_ephemeral_workspace(sandbox_path, sample_files=True)
        rec = WorkspaceRecord(
            id=DEFAULT_SANDBOX_WORKSPACE_ID,
            space_id=DEFAULT_PUBLIC_SPACE_ID,
            slug="sandbox",
            name="Public Ephemeral Sandbox",
            path=str(sandbox_path),
            visibility=Visibility.PUBLIC.value,
            is_ephemeral=True,
            storage_quota_bytes=settings.public_quota_bytes,
        )
        session.add(rec)
        session.commit()
        session.refresh(rec)
    else:
        ws = Space.reset_ephemeral_workspace(Path(rec.path), sample_files=True)

    ws_stats = ws.stats()
    return WorkspaceDetailResponse(
        id=rec.id,
        space_id=rec.space_id,
        slug=rec.slug,
        name=rec.name,
        path=rec.path,
        visibility=Visibility(rec.visibility),
        is_ephemeral=rec.is_ephemeral,
        storage_quota_bytes=rec.storage_quota_bytes,
        owner_id=rec.owner_id,
        team_id=rec.team_id,
        initialized=True,
        created_at=rec.created_at,
        used_bytes=ws_stats.total_size_bytes,
        stats=ws_stats,
    )


@router.get("/api-keys", response_model=list[ApiKeyInfo], summary="List issued API keys")
def list_api_keys(
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> list[ApiKeyInfo]:
    """List metadata for all database-managed User and Admin API keys."""
    records = session.scalars(select(ApiKeyRecord).order_by(ApiKeyRecord.created_at.desc())).all()
    return [_api_key_record_to_info(rec) for rec in records]


@router.post(
    "/api-keys",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyCreateResponse,
    summary="Issue a new User/Team or Admin API key",
)
def create_api_key(
    payload: ApiKeyCreateRequest,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> ApiKeyCreateResponse:
    """Generate and persist a new API key (raw secret is returned once)."""
    raw_key = generate_api_key(prefix="dartfx")
    key_id = str(uuid.uuid4())
    rec = ApiKeyRecord(
        id=key_id,
        key_prefix=raw_key[:15],
        key_hash=hash_api_key(raw_key),
        label=payload.label,
        tier=payload.tier.value,
        team_ids_json=json.dumps(payload.team_ids),
        allowed_space_ids_json=json.dumps(payload.allowed_space_ids),
        is_active=True,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)

    return ApiKeyCreateResponse(key=raw_key, info=_api_key_record_to_info(rec))


@router.delete("/api-keys/{key_id}", summary="Revoke/delete an API key")
def revoke_api_key(
    key_id: str,
    _admin: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Revoke and remove a database-managed API key."""
    rec = session.get(ApiKeyRecord, key_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"API key '{key_id}' not found.")
    session.delete(rec)
    session.commit()
    return {"id": key_id, "status": "revoked"}
