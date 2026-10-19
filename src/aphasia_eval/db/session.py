"""Thread-safe SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base, Run


class Database:
    """Own the engine and issue one short-lived session per unit of work."""

    def __init__(self, url: str) -> None:
        _prepare_sqlite_directory(url)
        engine_options: dict[str, object] = {}
        parsed = make_url(url)
        if parsed.get_backend_name() == "sqlite":
            engine_options["connect_args"] = {"check_same_thread": False}
            if parsed.database in {None, "", ":memory:"}:
                engine_options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **engine_options)
        if parsed.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def create_all(self) -> None:
        """Create the schema and apply safe additive compatibility changes."""

        Base.metadata.create_all(self.engine)
        self._ensure_additive_columns()

    def _ensure_additive_columns(self) -> None:
        """Keep pre-M7 SQLite databases readable without rebuilding tables."""

        if self.engine.dialect.name != "sqlite":
            return
        columns = {column["name"] for column in inspect(self.engine).get_columns("aggregates")}
        if "condition_id" not in columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE aggregates ADD COLUMN condition_id "
                        "VARCHAR(100) NOT NULL DEFAULT 'baseline'"
                    )
                )

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit a transaction on success and always close its session."""

        database_session = self.session_factory()
        try:
            yield database_session
            database_session.commit()
        except Exception:
            database_session.rollback()
            raise
        finally:
            database_session.close()

    def mark_stale_runs_interrupted(self) -> int:
        """Mark runs left active by a previous process as interrupted."""

        with self.session() as database_session:
            outcome = database_session.execute(
                update(Run)
                .where(Run.status.in_(("queued", "running")))
                .values(status="interrupted", finished_at=datetime.now(UTC))
            )
            return outcome.rowcount or 0

    def close(self) -> None:
        """Release pooled database connections."""

        self.engine.dispose()


def _prepare_sqlite_directory(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in {None, "", ":memory:"}:
        return
    Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
