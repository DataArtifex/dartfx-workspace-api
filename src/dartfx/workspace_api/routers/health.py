"""Health, readiness, build info, and system tools diagnostics endpoints."""

from pathlib import Path

from fastapi import APIRouter, Depends

from dartfx.workspace.models import Principal
from dartfx.workspace.tasks import detect_installed_tools
from workspace_api.__about__ import __version__
from workspace_api.dependencies import require_admin
from workspace_api.models import StatusResponse

router = APIRouter(tags=["health"])


def _get_release_id() -> str:
    build_file = Path(__file__).resolve().parent.parent / "build_time.txt"
    if build_file.exists():
        content = build_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    return "dev"


@router.get("/status", response_model=StatusResponse, summary="Service health and release metadata")
def get_status() -> StatusResponse:
    """Return service status, version, and container build timestamp."""
    return StatusResponse(status="pass", version=__version__, releaseId=_get_release_id())


@router.get("/healthz", response_model=StatusResponse, summary="Container liveness & readiness probe")
def healthz() -> StatusResponse:
    """Liveness/readiness endpoint probed by Docker, Kubernetes, and reverse proxies."""
    return get_status()


@router.get("/system/tools", summary="Inspect external workspace CLI tools (Admin)")
def get_system_tools(_admin: Principal = Depends(require_admin)) -> dict[str, dict[str, str | bool | None]]:
    """Detect installed workspace CLI tools (qsv, readstat, extract_metadata) and analytical engines."""
    return detect_installed_tools()
