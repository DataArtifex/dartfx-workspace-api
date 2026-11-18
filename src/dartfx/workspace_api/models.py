"""Pydantic v2 request and response schemas for dartfx-workspace-api."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from dartfx.workspace.models import (
    AccessTier,
    SpaceStats,
    TaskStatus,
    TaskType,
    Visibility,
    WorkspaceStats,
)


class StatusResponse(BaseModel):
    """Canonical health and release status payload."""

    status: str = "pass"
    version: str
    releaseId: str


class SpaceCreateRequest(BaseModel):
    """Request payload for creating or registering a virtual Space."""

    id: str | None = Field(default=None, description="Optional explicit Space identifier")
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    root_path: str | None = Field(default=None, description="Optional host directory path")
    storage_quota_bytes: int | None = Field(default=None, ge=0)
    visibility: Visibility = Visibility.PRIVATE


class SpaceResponse(BaseModel):
    """Public Space representation with optional aggregate stats."""

    id: str
    slug: str
    name: str
    description: str
    root_path: str
    storage_quota_bytes: int | None
    visibility: Visibility
    created_at: datetime
    stats: SpaceStats | None = None


class WorkspaceCreateRequest(BaseModel):
    """Request payload for creating or registering a Workspace."""

    id: str | None = Field(default=None, description="Optional explicit Workspace identifier")
    space_id: str | None = Field(default="public")
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    path: str | None = Field(default=None, description="Optional explicit host filesystem path")
    visibility: Visibility = Visibility.PRIVATE
    is_ephemeral: bool = False
    storage_quota_bytes: int | None = Field(default=None, ge=0)
    owner_id: str | None = None
    team_id: str | None = None
    initialize: bool = True
    create_dirs: bool = True


class WorkspaceResponse(BaseModel):
    """Workspace summary record."""

    id: str
    space_id: str | None
    slug: str
    name: str
    path: str
    visibility: Visibility
    is_ephemeral: bool
    storage_quota_bytes: int | None
    owner_id: str | None
    team_id: str | None
    initialized: bool
    created_at: datetime


class WorkspaceDetailResponse(WorkspaceResponse):
    """Detailed Workspace view including usage and KB stats."""

    used_bytes: int = 0
    stats: WorkspaceStats | None = None


class WorkspaceInitRequest(BaseModel):
    """Options for initializing a workspace."""

    create_dirs: bool = True


class ApiKeyCreateRequest(BaseModel):
    """Request payload for issuing a new API key."""

    label: str = Field(..., min_length=1, max_length=255)
    tier: AccessTier = AccessTier.USER
    team_ids: list[str] = Field(default_factory=list)
    allowed_space_ids: list[str] = Field(default_factory=list)


class ApiKeyInfo(BaseModel):
    """API key metadata (without secret value)."""

    id: str
    key_prefix: str
    label: str
    tier: AccessTier
    team_ids: list[str]
    allowed_space_ids: list[str]
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None


class ApiKeyCreateResponse(BaseModel):
    """Returned once upon API key creation, containing the raw secret key."""

    key: str
    info: ApiKeyInfo


class ScanJobRequest(BaseModel):
    """Request payload to queue an asynchronous workspace scan."""

    target_path: str = "."
    clean: bool = False
    webhook_url: str | None = None


class FairifyJobRequest(BaseModel):
    """Request payload to queue an asynchronous qsv fairify workflow."""

    source_path: str
    describe: bool = False
    force: bool = False
    webhook_url: str | None = None


class ReadstatJobRequest(BaseModel):
    """Request payload to queue an asynchronous readstat / tocsv operation."""

    source_path: str
    output_path: str | None = None
    extract_metadata: bool = False
    to_csv: bool = False
    force: bool = False
    webhook_url: str | None = None


class JobResponse(BaseModel):
    """Asynchronous job state, progress, results, and log output."""

    id: str
    workspace_id: str
    task_type: TaskType
    status: TaskStatus
    progress_pct: float
    current_step: str
    result: dict[str, Any] | None = None
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    webhook_url: str | None = None
    submitted_by: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SparqlQueryRequest(BaseModel):
    """Read-only SPARQL query payload."""

    query: str = Field(..., min_length=1)


class TabularQueryRequest(BaseModel):
    """Read-only SQL query payload over workspace tabular files."""

    sql: str = Field(..., min_length=1)
    max_rows: int = Field(default=500, ge=1, le=10000)


class MkdirRequest(BaseModel):
    """Directory creation payload."""

    path: str = Field(..., min_length=1)


class MoveCopyRequest(BaseModel):
    """Move/rename or copy payload."""

    src_path: str = Field(..., min_length=1)
    dst_path: str = Field(..., min_length=1)


class ResourceUpdateRequest(BaseModel):
    """Manual classification or attribute update payload for a KB resource."""

    file_type: str | None = None
    file_format: str | None = None
    attributes: dict[str, str] | None = None
