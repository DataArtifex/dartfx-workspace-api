"""In-process asynchronous JobManager bridging dartfx.workspace.tasks to REST, SSE, WebSockets, and Webhooks."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from dartfx.workspace.core import Workspace
from dartfx.workspace.models import TaskEvent, TaskStatus, TaskType
from dartfx.workspace.tasks import (
    run_fairify_task,
    run_readstat_task,
    run_scan_task,
    run_tocsv_task,
)
from workspace_api.db import Database
from workspace_api.models import JobResponse
from workspace_api.orm import JobRecord

logger = logging.getLogger(__name__)


def job_record_to_response(record: JobRecord) -> JobResponse:
    """Convert an ORM JobRecord to a Pydantic JobResponse."""
    logs = [line for line in record.logs_text.splitlines() if line] if record.logs_text else []
    result = json.loads(record.result_json) if record.result_json else None
    return JobResponse(
        id=record.id,
        workspace_id=record.workspace_id,
        task_type=TaskType(record.task_type),
        status=TaskStatus(record.status),
        progress_pct=record.progress_pct,
        current_step=record.current_step,
        result=result,
        error=record.error_message,
        logs=logs,
        webhook_url=record.webhook_url,
        submitted_by=record.submitted_by,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


class JobManager:
    """Manages async workspace background jobs, DB persistence, pub/sub streams, and webhook callbacks."""

    def __init__(self, db: Database):
        self.db = db
        self._job_subscribers: dict[str, set[asyncio.Queue[TaskEvent]]] = {}
        self._global_subscribers: set[asyncio.Queue[TaskEvent]] = set()
        self._cancel_flags: dict[str, bool] = {}
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    def subscribe_job(self, job_id: str) -> asyncio.Queue[TaskEvent]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self._job_subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe_job(self, job_id: str, queue: asyncio.Queue[TaskEvent]) -> None:
        subs = self._job_subscribers.get(job_id)
        if subs:
            subs.discard(queue)
            if not subs:
                self._job_subscribers.pop(job_id, None)

    def subscribe_global(self) -> asyncio.Queue[TaskEvent]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self._global_subscribers.add(queue)
        return queue

    def unsubscribe_global(self, queue: asyncio.Queue[TaskEvent]) -> None:
        self._global_subscribers.discard(queue)

    def get_job(self, job_id: str) -> JobResponse | None:
        with self.db.SessionLocal() as session:
            rec = session.get(JobRecord, job_id)
            return job_record_to_response(rec) if rec else None

    async def submit_job(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        task_type: TaskType,
        params: dict[str, Any],
        submitted_by: str,
        webhook_url: str | None = None,
    ) -> JobResponse:
        """Persist a pending job record and schedule background execution."""
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with self.db.SessionLocal() as session:
            rec = JobRecord(
                id=job_id,
                workspace_id=workspace_id,
                task_type=task_type.value,
                status=TaskStatus.PENDING.value,
                progress_pct=0.0,
                current_step="Queued",
                params_json=json.dumps(params),
                webhook_url=webhook_url,
                submitted_by=submitted_by,
                created_at=now,
            )
            session.add(rec)
            session.commit()
            session.refresh(rec)
            initial = job_record_to_response(rec)

        self._cancel_flags[job_id] = False
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(
            self._run_job(
                job_id=job_id,
                workspace_id=workspace_id,
                workspace_path=workspace_path,
                task_type=task_type,
                params=params,
                webhook_url=webhook_url,
                loop=loop,
            )
        )
        self._active_tasks[job_id] = task
        return initial

    async def _run_job(
        self,
        *,
        job_id: str,
        workspace_id: str,
        workspace_path: Path,
        task_type: TaskType,
        params: dict[str, Any],
        webhook_url: str | None,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        def _is_cancelled() -> bool:
            return self._cancel_flags.get(job_id, False)

        def _on_event(event: TaskEvent) -> None:
            loop.call_soon_threadsafe(self._process_event_sync_in_loop, event)

        def _execute() -> TaskEvent:
            ws = Workspace(workspace_path)
            if task_type == TaskType.SCAN:
                return run_scan_task(
                    ws,
                    task_id=job_id,
                    workspace_id=workspace_id,
                    target_path=str(params.get("target_path", ".")),
                    clean=bool(params.get("clean", False)),
                    on_event=_on_event,
                    cancel_check=_is_cancelled,
                )
            if task_type == TaskType.FAIRIFY:
                return run_fairify_task(
                    ws,
                    task_id=job_id,
                    workspace_id=workspace_id,
                    source_rel_path=str(params["source_path"]),
                    describe=bool(params.get("describe", False)),
                    on_event=_on_event,
                    cancel_check=_is_cancelled,
                )
            if task_type == TaskType.TOCSV:
                return run_tocsv_task(
                    ws,
                    task_id=job_id,
                    workspace_id=workspace_id,
                    source_rel_path=str(params["source_path"]),
                    force=bool(params.get("force", False)),
                    on_event=_on_event,
                    cancel_check=_is_cancelled,
                )
            return run_readstat_task(
                ws,
                task_id=job_id,
                workspace_id=workspace_id,
                source_rel_path=str(params["source_path"]),
                output_rel_path=params.get("output_path"),
                extract_metadata=bool(params.get("extract_metadata", False)),
                force=bool(params.get("force", False)),
                on_event=_on_event,
                cancel_check=_is_cancelled,
            )

        try:
            final_event = await asyncio.to_thread(_execute)
            if webhook_url:
                await self._dispatch_webhook(webhook_url, final_event)
        except Exception as exc:
            logger.exception("Unhandled error executing job %s", job_id)
            err_event = TaskEvent(
                task_id=job_id,
                workspace_id=workspace_id,
                task_type=task_type,
                status=TaskStatus.FAILED,
                progress_pct=100.0,
                current_step="Failed",
                log_line=f"Unhandled error: {exc}",
                error=str(exc),
            )
            self._process_event_sync_in_loop(err_event)
            if webhook_url:
                await self._dispatch_webhook(webhook_url, err_event)
        finally:
            self._active_tasks.pop(job_id, None)
            self._cancel_flags.pop(job_id, None)

    def _process_event_sync_in_loop(self, event: TaskEvent) -> None:
        """Persist TaskEvent to DB and push to all active SSE/WebSocket queues."""
        with self.db.SessionLocal() as session:
            rec = session.get(JobRecord, event.task_id)
            if rec is not None:
                now = datetime.now(UTC)
                if rec.started_at is None and event.status == TaskStatus.RUNNING:
                    rec.started_at = now
                rec.status = event.status.value
                rec.progress_pct = event.progress_pct
                rec.current_step = event.current_step
                if event.log_line:
                    rec.logs_text = f"{rec.logs_text}\n{event.log_line}".lstrip("\n")
                if event.result is not None:
                    rec.result_json = json.dumps(event.result)
                if event.error is not None:
                    rec.error_message = event.error
                if event.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    rec.completed_at = now
                session.commit()

        for q in list(self._job_subscribers.get(event.task_id, ())):
            q.put_nowait(event)
        for q in list(self._global_subscribers):
            q.put_nowait(event)

    @staticmethod
    async def _dispatch_webhook(webhook_url: str, event: TaskEvent) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(webhook_url, json=event.model_dump(mode="json"))
        except Exception as exc:
            logger.warning("Webhook delivery to %s failed: %s", webhook_url, exc)

    def cancel_job(self, job_id: str) -> JobResponse | None:
        """Request cancellation for a pending or running job."""
        self._cancel_flags[job_id] = True
        with self.db.SessionLocal() as session:
            rec = session.get(JobRecord, job_id)
            if rec is None:
                return None
            if rec.status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
                rec.status = TaskStatus.CANCELLED.value
                rec.current_step = "Cancellation requested"
                rec.completed_at = datetime.now(UTC)
                rec.logs_text = f"{rec.logs_text}\nCancellation requested.".lstrip("\n")
                session.commit()
                session.refresh(rec)
                cancel_event = TaskEvent(
                    task_id=rec.id,
                    workspace_id=rec.workspace_id,
                    task_type=TaskType(rec.task_type),
                    status=TaskStatus.CANCELLED,
                    progress_pct=rec.progress_pct,
                    current_step=rec.current_step,
                    log_line="Cancellation requested.",
                )
                for q in list(self._job_subscribers.get(rec.id, ())):
                    q.put_nowait(cancel_event)
                for q in list(self._global_subscribers):
                    q.put_nowait(cancel_event)
            return job_record_to_response(rec)

    async def shutdown(self) -> None:
        """Wait briefly for in-flight tasks during application shutdown."""
        tasks = list(self._active_tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
