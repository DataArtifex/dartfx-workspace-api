"""Configuration management for dartfx-workspace-api."""

import os
from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    """Runtime settings parsed from environment variables."""

    database_url: str
    storage_root: Path
    public_quota_bytes: int
    user_api_key: str | None = None
    admin_api_key: str | None = None
    cors_origins: str = "*"


def get_settings() -> Settings:
    """Build current settings snapshot from environment variables."""
    quota_bytes_raw = os.getenv("DARTFX_PUBLIC_QUOTA_BYTES", "").strip()
    if quota_bytes_raw:
        public_quota_bytes = int(quota_bytes_raw)
    else:
        quota_mb = int(os.getenv("DARTFX_PUBLIC_QUOTA_MB", "50"))
        public_quota_bytes = quota_mb * 1024 * 1024

    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./.dartfx-api/registry.db").strip(),
        storage_root=Path(os.getenv("DARTFX_STORAGE_ROOT", "./.dartfx-api/storage")).resolve(),
        public_quota_bytes=public_quota_bytes,
        user_api_key=os.getenv("DARTFX_USER_API_KEY", "").strip() or None,
        admin_api_key=os.getenv("DARTFX_ADMIN_API_KEY", "").strip() or None,
        cors_origins=os.getenv("CORS_ORIGINS", "*").strip(),
    )
