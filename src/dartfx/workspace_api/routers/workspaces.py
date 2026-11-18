"""Spaces, Workspaces listing, stats, and async task triggers (scan, fairify, readstat)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dartfx.workspace.access import AccessPolicy
from dartfx.workspace.core import Workspace
from dartfx.workspace.models import Action, Principal, TaskEvent, TaskType, WorkspaceStats
from dartfx.workspace.spaces import Space
from dartfx.workspace.utils import (
    SUPPORTED_FAIRIFY_SUFFIXES,
    SUPPORTED_READSTAT_SUFFIXES,
    detect_system_generated,
    is_fairifiable_file,
    is_statistical_file,
)
from workspace_api.db import space_record_to_config, workspace_record_to_config
from workspace_api.dependencies import (
    get_current_principal,
    get_db_session,
    get_job_manager,
    map_domain_error,
    resolve_authorized_workspace,
)
from workspace_api.jobs import JobManager
from workspace_api.models import (
    FairifyJobRequest,
    JobResponse,
    ReadstatJobRequest,
    ScanJobRequest,
    SpaceResponse,
    WorkspaceDetailResponse,
    WorkspaceResponse,
)
from workspace_api.orm import SpaceRecord, WorkspaceRecord

router = APIRouter(tags=["workspaces"])

callback_router = APIRouter()


@callback_router.post(
    "{$request.body#/webhook_url}",
    summary="Webhook callback invoked upon async job completion or failure",
)
def job_webhook_callback(_event: TaskEvent) -> dict[str, str]:
    """OpenAPI callback specification for async job completion webhooks."""
    return {"received": "ok"}


@router.get("/spaces", response_model=list[SpaceResponse], summary="List accessible Spaces")
def list_spaces(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> list[SpaceResponse]:
    """Return all Spaces visible to the current Principal along with aggregate stats."""
    records = session.scalars(select(SpaceRecord).order_by(SpaceRecord.created_at)).all()
    results: list[SpaceResponse] = []
    for rec in records:
        cfg = space_record_to_config(rec)
        if not AccessPolicy.can_access_space(principal, cfg, Action.READ):
            continue
        ws_records = session.scalars(select(WorkspaceRecord).where(WorkspaceRecord.space_id == rec.id)).all()
        visible_workspaces = [
            Workspace(workspace_record_to_config(w).path)
            for w in ws_records
            if AccessPolicy.can_access_workspace(principal, workspace_record_to_config(w), Action.READ)
        ]
        stats = Space(cfg).stats(visible_workspaces)
        results.append(
            SpaceResponse(
                id=rec.id,
                slug=rec.slug,
                name=rec.name,
                description=rec.description,
                root_path=rec.root_path,
                storage_quota_bytes=rec.storage_quota_bytes,
                visibility=cfg.visibility,
                created_at=rec.created_at,
                stats=stats,
            )
        )
    return results


@router.get("/workspaces", response_model=list[WorkspaceResponse], summary="List accessible Workspaces")
def list_workspaces(
    space_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> list[WorkspaceResponse]:
    """List registered workspaces authorized for the current Principal."""
    stmt = select(WorkspaceRecord).order_by(WorkspaceRecord.created_at)
    if space_id is not None:
        stmt = stmt.where(WorkspaceRecord.space_id == space_id)

    records = session.scalars(stmt).all()
    items: list[WorkspaceResponse] = []
    for rec in records:
        cfg = workspace_record_to_config(rec)
        if not AccessPolicy.can_access_workspace(principal, cfg, Action.READ):
            continue
        ws = Workspace(cfg.path)
        items.append(
            WorkspaceResponse(
                id=rec.id,
                space_id=rec.space_id,
                slug=rec.slug,
                name=rec.name,
                path=rec.path,
                visibility=cfg.visibility,
                is_ephemeral=rec.is_ephemeral,
                storage_quota_bytes=rec.storage_quota_bytes,
                owner_id=rec.owner_id,
                team_id=rec.team_id,
                initialized=ws.is_initialized(),
                created_at=rec.created_at,
            )
        )
    return items


@router.get(
    "/workspaces/{ws_id}",
    response_model=WorkspaceDetailResponse,
    summary="Get workspace details and storage metrics",
)
def get_workspace(
    ws_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> WorkspaceDetailResponse:
    """Retrieve metadata, quota telemetry, and stats for a specific workspace."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    rec = auth_ws.record
    ws = auth_ws.workspace
    initialized = ws.is_initialized()
    ws_stats = ws.stats() if initialized else None
    used_bytes = ws_stats.total_size_bytes if ws_stats else ws.total_usage_bytes()

    return WorkspaceDetailResponse(
        id=rec.id,
        space_id=rec.space_id,
        slug=rec.slug,
        name=rec.name,
        path=rec.path,
        visibility=auth_ws.config.visibility,
        is_ephemeral=rec.is_ephemeral,
        storage_quota_bytes=rec.storage_quota_bytes,
        owner_id=rec.owner_id,
        team_id=rec.team_id,
        initialized=initialized,
        created_at=rec.created_at,
        used_bytes=used_bytes,
        stats=ws_stats,
    )


@router.get("/workspaces/{ws_id}/stats", response_model=WorkspaceStats, summary="Get workspace file statistics")
def get_workspace_stats(
    ws_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> WorkspaceStats:
    """Compute file categorization and KB registration health statistics for a workspace."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.stats()
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.post(
    "/workspaces/{ws_id}/scan",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    callbacks=callback_router.routes,
    summary="Trigger asynchronous workspace scan and KB synchronization",
)
async def trigger_scan(
    ws_id: str,
    payload: ScanJobRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """Queue an asynchronous scan job returning 202 Accepted and a Location URL to poll/stream progress."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.EXECUTE, session, principal)
    if not auth_ws.workspace.is_initialized():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not initialized.")

    try:
        auth_ws.workspace.resolve_safe_path(payload.target_path)
    except Exception as exc:
        raise map_domain_error(exc) from exc

    job = await job_manager.submit_job(
        workspace_id=auth_ws.record.id,
        workspace_path=auth_ws.workspace.path,
        task_type=TaskType.SCAN,
        params={"target_path": payload.target_path, "clean": payload.clean},
        submitted_by=principal.id,
        webhook_url=payload.webhook_url,
    )
    prefix = str(request.scope.get("root_path", "")).rstrip("/")
    response.headers["Location"] = f"{prefix}/jobs/{job.id}"
    return job


@router.post(
    "/workspaces/{ws_id}/fairify",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    callbacks=callback_router.routes,
    summary="Trigger asynchronous qsv fairify profiling pipeline",
)
async def trigger_fairify(
    ws_id: str,
    payload: FairifyJobRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """Queue an asynchronous qsv fairify job (stats, schema, frequency, optional describegpt)."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.EXECUTE, session, principal)
    try:
        target = auth_ws.workspace.resolve_safe_path(payload.source_path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Source file not found: {payload.source_path}")

        source_is_gen, _ = detect_system_generated(target)
        if auth_ws.workspace.is_initialized():
            rel_str = target.relative_to(auth_ws.workspace.path).as_posix()
            kb_entry = auth_ws.workspace.kb.get_file_by_path(rel_str)
            if kb_entry and kb_entry.get("is_generated"):
                source_is_gen = True

        if source_is_gen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot fairify system-generated artifact '{payload.source_path}'. "
                    "Only user-provided tabular files can be FAIRified."
                ),
            )

        if not is_fairifiable_file(target, is_generated=source_is_gen):
            allowed = "|".join(ext.lstrip(".") for ext in sorted(SUPPORTED_FAIRIFY_SUFFIXES))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported format for fairify: '{target.suffix}'. Expected one of: {allowed}",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise map_domain_error(exc) from exc

    job = await job_manager.submit_job(
        workspace_id=auth_ws.record.id,
        workspace_path=auth_ws.workspace.path,
        task_type=TaskType.FAIRIFY,
        params={"source_path": payload.source_path, "describe": payload.describe},
        submitted_by=principal.id,
        webhook_url=payload.webhook_url,
    )
    prefix = str(request.scope.get("root_path", "")).rstrip("/")
    response.headers["Location"] = f"{prefix}/jobs/{job.id}"
    return job


@router.post(
    "/workspaces/{ws_id}/readstat",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    callbacks=callback_router.routes,
    summary="Trigger asynchronous readstat / tocsv conversion or metadata extraction",
)
async def trigger_readstat(
    ws_id: str,
    payload: ReadstatJobRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """Queue an asynchronous readstat conversion, tocsv, or extract_metadata job."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.EXECUTE, session, principal)
    try:
        target = auth_ws.workspace.resolve_safe_path(payload.source_path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Source file not found: {payload.source_path}")

        if not is_statistical_file(target):
            allowed = "|".join(ext.lstrip(".") for ext in sorted(SUPPORTED_READSTAT_SUFFIXES))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported format for readstat/to_csv: '{target.suffix}'. "
                    f"Expected one of: {allowed} (SAS, Stata, SPSS)."
                ),
            )

        if payload.output_path:
            auth_ws.workspace.resolve_safe_path(payload.output_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_domain_error(exc) from exc

    task_type = TaskType.TOCSV if payload.to_csv else TaskType.READSTAT
    job = await job_manager.submit_job(
        workspace_id=auth_ws.record.id,
        workspace_path=auth_ws.workspace.path,
        task_type=task_type,
        params={
            "source_path": payload.source_path,
            "output_path": payload.output_path,
            "extract_metadata": payload.extract_metadata,
            "force": payload.force,
        },
        submitted_by=principal.id,
        webhook_url=payload.webhook_url,
    )
    prefix = str(request.scope.get("root_path", "")).rstrip("/")
    response.headers["Location"] = f"{prefix}/jobs/{job.id}"
    return job
