"""Authentication identity introspection endpoint."""

from fastapi import APIRouter, Depends

from dartfx.workspace.models import Principal
from workspace_api.dependencies import get_current_principal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/whoami", response_model=Principal, summary="Inspect active caller tier and permissions")
def whoami(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Return the active 3-tier Principal resolved from the request credentials."""
    return principal
