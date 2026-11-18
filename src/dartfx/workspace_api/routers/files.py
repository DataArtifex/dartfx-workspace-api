"""Workspace file browsing, tree view, preview, upload, download, ZIP archive, and filesystem operations."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from dartfx.workspace.models import Action, FileEntry, FilePreview, Principal, TreeNode
from workspace_api.dependencies import (
    get_current_principal,
    get_db_session,
    map_domain_error,
    resolve_authorized_workspace,
)
from workspace_api.models import MkdirRequest, MoveCopyRequest

router = APIRouter(prefix="/workspaces/{ws_id}", tags=["files"])


@router.get("/files", response_model=list[FileEntry], summary="List and filter workspace files")
def list_workspace_files(
    ws_id: str,
    path: str = Query(default="."),
    recursive: bool = Query(default=False),
    show_all: bool = Query(default=False),
    glob: str | None = Query(default=None),
    type: str | None = Query(default=None),
    format: str | None = Query(default=None),
    mime: str | None = Query(default=None),
    registered: bool | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> list[FileEntry]:
    """List directory contents with optional recursive traversal and KB metadata filters."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.list_files(
            target_path=path,
            recursive=recursive,
            show_all=show_all,
            glob_pattern=glob,
            type_filter=type,
            format_filter=format,
            mime_filter=mime,
            registered_only=registered,
        )
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.get("/tree", response_model=TreeNode, summary="Get hierarchical directory tree")
def get_workspace_tree(
    ws_id: str,
    path: str = Query(default="."),
    max_level: int | None = Query(default=None, ge=1),
    type: str | None = Query(default=None),
    format: str | None = Query(default=None),
    mime: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> TreeNode:
    """Return a structured directory tree annotated with Knowledge Base registration status."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.get_tree(
            target_path=path,
            max_level=max_level,
            type_filter=type,
            format_filter=format,
            mime_filter=mime,
        )
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.get("/files/preview", response_model=FilePreview, summary="Preview first or last lines of a file")
def preview_workspace_file(
    ws_id: str,
    path: str = Query(..., min_length=1),
    mode: str = Query(default="head", pattern="^(head|tail)$"),
    lines: int = Query(default=10, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> FilePreview:
    """Inspect the beginning (head) or end (tail) of a workspace text file."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.preview_file(path, mode=mode, num_lines=lines)
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.post(
    "/files/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=list[FileEntry],
    summary="Upload one or more files into the workspace",
)
def upload_workspace_files(
    ws_id: str,
    files: list[UploadFile] = File(...),
    target_dir: str = Form(default="/"),
    overwrite: bool = Form(default=False),
    auto_scan: bool = Form(default=True),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> list[FileEntry]:
    """Upload file(s) with streaming quota enforcement and automatic Knowledge Base registration."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.WRITE, session, principal)
    ws = auth_ws.workspace
    uploaded: list[FileEntry] = []

    try:
        target_base = ws.resolve_safe_path(target_dir)
        if target_base.exists() and not target_base.is_dir():
            raise ValueError(f"target_dir '{target_dir}' is not a directory.")

        base_rel = "" if target_base == ws.path else target_base.relative_to(ws.path).as_posix()
        for upload in files:
            safe_name = Path(upload.filename or "upload.bin").name
            if not safe_name or safe_name in (".", ".."):
                raise ValueError(f"Invalid filename: '{upload.filename}'")
            dest_rel = f"{base_rel}/{safe_name}" if base_rel else safe_name
            entry = ws.ingest_file(
                upload.file,
                dest_rel,
                overwrite=overwrite,
                auto_scan=auto_scan,
                quota_bytes=auth_ws.effective_quota_bytes,
            )
            uploaded.append(entry)
    except Exception as exc:
        raise map_domain_error(exc) from exc
    finally:
        for upload in files:
            upload.file.close()

    return uploaded


@router.get("/files/download", summary="Download a single file by path or resource UUID")
def download_workspace_file(
    ws_id: str,
    path: str | None = Query(default=None),
    uuid: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    """Stream a single workspace file with MIME content-type and BLAKE3 ETag when indexed."""
    if not path and not uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'path' or 'uuid' query parameter.",
        )

    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    identifier = uuid or path or ""
    try:
        abs_path, mime_type = auth_ws.workspace.resolve_download_path(identifier)
    except Exception as exc:
        raise map_domain_error(exc) from exc

    headers: dict[str, str] = {}
    if auth_ws.workspace.is_initialized():
        rel = abs_path.relative_to(auth_ws.workspace.path).as_posix()
        kb_info = auth_ws.workspace.kb.get_file_by_path(rel)
        if kb_info and kb_info.get("blake3_hash"):
            headers["ETag"] = f'"{kb_info["blake3_hash"]}"'

    return FileResponse(
        path=abs_path,
        media_type=mime_type,
        filename=abs_path.name,
        headers=headers,
    )


@router.get("/files/archive", summary="Download a folder or workspace as a ZIP archive")
def download_workspace_archive(
    ws_id: str,
    path: str = Query(default="."),
    include_kb: bool = Query(default=False),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    """Package a workspace directory or entire workspace as a downloadable ZIP archive."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        zip_path = auth_ws.workspace.create_archive(rel_path=path, include_kb=include_kb)
    except Exception as exc:
        raise map_domain_error(exc) from exc

    cleaned_name = Path(path.strip("/")).name if path not in ("", ".", "/") else auth_ws.record.slug
    download_name = f"{cleaned_name or auth_ws.record.slug}.zip"

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )


@router.post("/files/mkdir", status_code=status.HTTP_201_CREATED, summary="Create directory inside workspace")
def mkdir_workspace_path(
    ws_id: str,
    payload: MkdirRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Create a directory inside the workspace."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.WRITE, session, principal)
    try:
        created = auth_ws.workspace.mkdir(payload.path)
        rel = created.relative_to(auth_ws.workspace.path).as_posix()
        return {"path": rel, "status": "created"}
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.post("/files/move", summary="Move or rename a file or directory")
def move_workspace_path(
    ws_id: str,
    payload: MoveCopyRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Move or rename a workspace file/directory while preserving KB resource UUIDs."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.WRITE, session, principal)
    try:
        dst = auth_ws.workspace.move_path(payload.src_path, payload.dst_path)
        rel = dst.relative_to(auth_ws.workspace.path).as_posix()
        return {"path": rel, "status": "moved"}
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.post("/files/copy", status_code=status.HTTP_201_CREATED, summary="Copy a file or directory")
def copy_workspace_path(
    ws_id: str,
    payload: MoveCopyRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Copy a workspace file or directory with quota enforcement and KB indexing."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.WRITE, session, principal)
    try:
        dst = auth_ws.workspace.copy_path(
            payload.src_path,
            payload.dst_path,
            quota_bytes=auth_ws.effective_quota_bytes,
        )
        rel = dst.relative_to(auth_ws.workspace.path).as_posix()
        return {"path": rel, "status": "copied"}
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.delete("/files", summary="Remove a file or directory from workspace and KB")
def delete_workspace_path(
    ws_id: str,
    path: str = Query(..., min_length=1),
    delete_artifacts: bool = Query(
        default=True,
        description="Whether to also remove related FAIRification / system-generated artifacts",
    ),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Delete a file or directory inside the workspace and prune its KB entries."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.WRITE, session, principal)
    try:
        removed = auth_ws.workspace.remove_path(path, delete_artifacts=delete_artifacts)
        norm_path = path.strip("/")
        deleted_artifacts = [p for p in removed if p != norm_path and p != path]
        return {
            "path": path,
            "status": "deleted",
            "deleted_artifacts": deleted_artifacts,
        }
    except Exception as exc:
        raise map_domain_error(exc) from exc
