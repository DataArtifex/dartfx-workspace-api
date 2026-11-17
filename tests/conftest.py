"""Shared test fixtures for dartfx-workspace-api."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workspace_api.config import Settings
from workspace_api.main import create_app

ADMIN_KEY = "test-admin-secret-key"
USER_KEY = "test-user-secret-key"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    db_path = (tmp_path / "registry.db").resolve()
    storage_root = (tmp_path / "storage").resolve()
    return Settings(
        database_url=f"sqlite:///{db_path}",
        storage_root=storage_root,
        public_quota_bytes=4096,
        user_api_key=USER_KEY,
        admin_api_key=ADMIN_KEY,
        cors_origins="*",
    )


@pytest.fixture
def client(test_settings: Settings) -> Generator[TestClient, None, None]:
    app = create_app(custom_settings=test_settings)
    with TestClient(app) as test_client:
        yield test_client
