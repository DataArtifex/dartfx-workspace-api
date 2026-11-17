"""3-tier authentication (Anonymous, User/Team, Admin) for dartfx-workspace-api."""

import hashlib
import json
import secrets
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dartfx.workspace.models import AccessTier, Principal
from workspace_api.config import Settings
from workspace_api.orm import ApiKeyRecord


def hash_api_key(raw_key: str) -> str:
    """Compute a deterministic SHA-256 digest for storing API keys securely."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "dfx") -> str:
    """Generate a high-entropy API key token."""
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def extract_raw_api_key(
    x_api_key: str | None = None,
    authorization: str | None = None,
    api_key_query: str | None = None,
) -> str | None:
    """Extract an API key from X-API-Key header, Bearer token, or query parameter."""
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.strip():
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    if api_key_query and api_key_query.strip():
        return api_key_query.strip()
    return None


def resolve_principal(raw_key: str | None, session: Session, settings: Settings) -> Principal:
    """Resolve an optional raw API key into a 3-tier domain Principal."""
    if not raw_key:
        return Principal(id="anonymous", tier=AccessTier.ANONYMOUS, label="Public Anonymous")

    if settings.admin_api_key and secrets.compare_digest(raw_key, settings.admin_api_key):
        return Principal(id="env-admin", tier=AccessTier.ADMIN, label="Environment Admin")

    if settings.user_api_key and secrets.compare_digest(raw_key, settings.user_api_key):
        return Principal(id="env-user", tier=AccessTier.USER, label="Environment User")

    digest = hash_api_key(raw_key)
    record = session.scalar(select(ApiKeyRecord).where(ApiKeyRecord.key_hash == digest))
    if record is None or not record.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key.",
        )

    record.last_used_at = datetime.now(UTC)
    session.commit()

    team_ids = json.loads(record.team_ids_json) if record.team_ids_json else []
    allowed_space_ids = json.loads(record.allowed_space_ids_json) if record.allowed_space_ids_json else []

    return Principal(
        id=f"key:{record.id}",
        tier=AccessTier(record.tier),
        label=record.label,
        team_ids=team_ids,
        allowed_space_ids=allowed_space_ids,
    )
