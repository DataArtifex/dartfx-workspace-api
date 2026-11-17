"""Async job polling (REST), Server-Sent Events (SSE), and WebSocket streaming endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from dartfx.workspace.access import AccessPolicy
from dartfx.workspace.models import Action, Principal, TaskEvent, TaskStatus, TaskType
from workspace_api.auth import extract_raw_api_key, resolve_principal
from workspace_api.config import Settings
from workspace_api.db import Database, workspace_record_to_config
from workspace_api.dependencies import (
    get_current_principal,
    get_db_instance,
    get_db_session,
    get_job_manager,
    resolve_authorized_workspace,
)
from workspace_api.jobs import JobManager, job_record_to_response
from workspace_api.models import JobResponse
from workspace_api.orm import JobRecord, WorkspaceRecord

router = APIRouter(tags=["jobs"])

_TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


@router.get("/jobs", response_model=list[JobResponse], summary="List background jobs visible to caller")
def list_jobs(
    workspace_id: str | None = Query(default=None),
    job_status: TaskStatus | None = Query(default=None, alias="status"),
    task_type: TaskType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> list[JobResponse]:
    """List recent asynchronous jobs filtered by caller permissions."""
    stmt = select(JobRecord).order_by(JobRecord.created_at.desc()).limit(limit)
    if workspace_id is not None:
        stmt = stmt.where(JobRecord.workspace_id == workspace_id)
    if job_status is not None:
        stmt = stmt.where(JobRecord.status == job_status.value)
    if task_type is not None:
        stmt = stmt.where(JobRecord.task_type == task_type.value)

    records = session.scalars(stmt).all()
    ws_cache: dict[str, bool] = {}
    visible: list[JobResponse] = []
    for rec in records:
        allowed = ws_cache.get(rec.workspace_id)
        if allowed is None:
            ws_rec = session.get(WorkspaceRecord, rec.workspace_id)
            if ws_rec is None:
                allowed = False
            else:
                allowed = AccessPolicy.can_access_workspace(principal, workspace_record_to_config(ws_rec), Action.READ)
            ws_cache[rec.workspace_id] = allowed
        if allowed:
            visible.append(job_record_to_response(rec))
    return visible


@router.get("/jobs/{job_id}", response_model=JobResponse, summary="Get job status, progress, and logs")
def get_job(
    job_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """Poll the state of a specific asynchronous background job."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")

    resolve_authorized_workspace(job.workspace_id, Action.READ, session, principal)
    return job


@router.get("/jobs/{job_id}/events", summary="Stream real-time TaskEvents over Server-Sent Events (SSE)")
async def stream_job_events(
    job_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
    job_manager: JobManager = Depends(get_job_manager),
) -> StreamingResponse:
    """Stream Server-Sent Events (`text/event-stream`) emitting TaskEvent JSON frames."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")

    resolve_authorized_workspace(job.workspace_id, Action.READ, session, principal)

    async def _event_generator() -> AsyncIterator[str]:
        queue = job_manager.subscribe_job(job_id)
        try:
            current = job_manager.get_job(job_id)
            if current is not None:
                snapshot = TaskEvent(
                    task_id=current.id,
                    workspace_id=current.workspace_id,
                    task_type=current.task_type,
                    status=current.status,
                    progress_pct=current.progress_pct,
                    current_step=current.current_step,
                    log_line=current.logs[-1] if current.logs else None,
                    result=current.result,
                    error=current.error,
                )
                yield f"event: task_event\ndata: {snapshot.model_dump_json()}\n\n"
                if current.status in _TERMINAL_STATUSES:
                    return

            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: task_event\ndata: {ev.model_dump_json()}\n\n"
                    if ev.status in _TERMINAL_STATUSES:
                        break
                except TimeoutError:
                    latest = job_manager.get_job(job_id)
                    if latest is not None and latest.status in _TERMINAL_STATUSES:
                        break
                    yield ": keepalive\n\n"
        finally:
            job_manager.unsubscribe_job(job_id, queue)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, summary="Cancel a pending or running job")
def cancel_job(
    job_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """Request cancellation of a background job."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")

    resolve_authorized_workspace(job.workspace_id, Action.EXECUTE, session, principal)
    cancelled = job_manager.cancel_job(job_id)
    if cancelled is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")
    return cancelled


@router.websocket("/ws/jobs")
async def jobs_websocket(
    websocket: WebSocket,
    api_key: str | None = Query(default=None),
) -> None:
    """Bi-directional WebSocket channel streaming authorized TaskEvents and accepting cancel/ping commands."""
    db: Database = websocket.app.state.db
    settings: Settings = websocket.app.state.settings
    job_manager: JobManager = websocket.app.state.job_manager

    raw_key = extract_raw_api_key(
        x_api_key=websocket.headers.get("x-api-key"),
        authorization=websocket.headers.get("authorization"),
        api_key_query=api_key,
    )

    try:
        with db.SessionLocal() as session:
            principal = resolve_principal(raw_key, session, settings)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = job_manager.subscribe_global()

    def _can_access(workspace_id: str, action: Action) -> bool:
        with db.SessionLocal() as session:
            ws_rec = session.get(WorkspaceRecord, workspace_id)
            if ws_rec is None:
                return False
            return AccessPolicy.can_access_workspace(principal, workspace_record_to_config(ws_rec), action)

    async def _sender() -> None:
        try:
            while True:
                event = await queue.get()
                if _can_access(event.workspace_id, Action.READ):
                    await websocket.send_json(event.model_dump(mode="json"))
        except (WebSocketDisconnect, asyncio.CancelledError):
            return

    async def _receiver() -> None:
        try:
            while True:
                raw_msg = await websocket.receive_text()
                try:
                    cmd: dict[str, Any] = json.loads(raw_msg)
                except json.JSONDecodeError:
                    await websocket.send_json({"error": "Invalid JSON command"})
                    continue

                action = cmd.get("action")
                if action == "ping":
                    await websocket.send_json({"action": "pong"})
                elif action == "cancel" and cmd.get("job_id"):
                    target_id = str(cmd["job_id"])
                    job = job_manager.get_job(target_id)
                    if job and _can_access(job.workspace_id, Action.EXECUTE):
                        updated = job_manager.cancel_job(target_id)
                        await websocket.send_json(
                            {"action": "cancelled", "job_id": target_id, "status": updated.status if updated else None}
                        )
                    else:
                        await websocket.send_json({"error": f"Job '{target_id}' not found or unauthorized"})
        except (WebSocketDisconnect, asyncio.CancelledError):
            return

    sender_task = asyncio.create_task(_sender())
    receiver_task = asyncio.create_task(_receiver())
    try:
        _done, pending = await asyncio.wait([sender_task, receiver_task], return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        job_manager.unsubscribe_global(queue)


# Keep get_db_instance imported/available cleanly if referenced in tests
__all__ = ["get_db_instance", "router"]
