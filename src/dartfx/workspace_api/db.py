"""Database engine, session factory, and default workspace seeding (SQLite & PostgreSQL)."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from dartfx.workspace.models import SpaceConfig, Visibility, WorkspaceConfig
from dartfx.workspace.spaces import Space
from workspace_api.config import Settings
from workspace_api.orm import Base, SpaceRecord, WorkspaceRecord

DEFAULT_PUBLIC_SPACE_ID = "public"
DEFAULT_SANDBOX_WORKSPACE_ID = "sandbox"


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy 2.0 engine configured for SQLite or PostgreSQL."""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        db_name = url.database
        if db_name and db_name != ":memory:":
            Path(db_name).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if db_name == ":memory:" or not db_name:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(database_url, **kwargs)

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    return create_engine(database_url, pool_pre_ping=True)


def space_record_to_config(record: SpaceRecord) -> SpaceConfig:
    """Convert an ORM SpaceRecord to a domain SpaceConfig."""
    return SpaceConfig(
        id=record.id,
        slug=record.slug,
        name=record.name,
        description=record.description,
        root_path=Path(record.root_path),
        storage_quota_bytes=record.storage_quota_bytes,
        visibility=Visibility(record.visibility),
    )


def workspace_record_to_config(record: WorkspaceRecord) -> WorkspaceConfig:
    """Convert an ORM WorkspaceRecord to a domain WorkspaceConfig."""
    return WorkspaceConfig(
        id=record.id,
        slug=record.slug,
        name=record.name,
        path=Path(record.path),
        space_id=record.space_id,
        visibility=Visibility(record.visibility),
        is_ephemeral=record.is_ephemeral,
        storage_quota_bytes=record.storage_quota_bytes,
        owner_id=record.owner_id,
        team_id=record.team_id,
    )


class Database:
    """Encapsulates SQLAlchemy engine, session factory, and default sandbox seeding."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = build_engine(settings.database_url)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def init_db(self) -> None:
        """Create schema tables if missing and provision default public space & ephemeral sandbox."""
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(bind=self.engine)

        with self.SessionLocal() as session:
            self.seed_defaults(session)

    def seed_defaults(self, session: Session) -> None:
        """Ensure the default public space and public ephemeral sandbox workspace exist."""
        public_root = (self.settings.storage_root / "public").resolve()
        public_root.mkdir(parents=True, exist_ok=True)

        space = session.get(SpaceRecord, DEFAULT_PUBLIC_SPACE_ID)
        if space is None:
            space = SpaceRecord(
                id=DEFAULT_PUBLIC_SPACE_ID,
                slug="public",
                name="Public Sandbox Space",
                description="Default public space hosting the ephemeral sandbox workspace.",
                root_path=str(public_root),
                storage_quota_bytes=self.settings.public_quota_bytes,
                visibility=Visibility.PUBLIC.value,
            )
            session.add(space)
            session.flush()

        sandbox = session.get(WorkspaceRecord, DEFAULT_SANDBOX_WORKSPACE_ID)
        if sandbox is None:
            sandbox_path = (public_root / "sandbox").resolve()
            Space.provision_ephemeral_workspace(sandbox_path, sample_files=True)
            sandbox = WorkspaceRecord(
                id=DEFAULT_SANDBOX_WORKSPACE_ID,
                space_id=DEFAULT_PUBLIC_SPACE_ID,
                slug="sandbox",
                name="Public Ephemeral Sandbox",
                path=str(sandbox_path),
                visibility=Visibility.PUBLIC.value,
                is_ephemeral=True,
                storage_quota_bytes=self.settings.public_quota_bytes,
            )
            session.add(sandbox)

        session.commit()

    def dispose(self) -> None:
        self.engine.dispose()
