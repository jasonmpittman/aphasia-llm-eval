from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, select

from aphasia_eval.db import Database, Run


def _run(run_id: str, status: str) -> Run:
    return Run(
        id=run_id,
        label=None,
        status=status,
        created_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
        settings_json={},
        app_version="0.1.0",
        litellm_host="https://gateway.invalid/v1",
        notes=None,
    )


def test_database_creates_schema_and_interrupts_stale_runs(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'nested/data.db'}")
    database.create_all()
    assert {
        "runs",
        "run_models",
        "run_tasks",
        "results",
        "aggregates",
    }.issubset(inspect(database.engine).get_table_names())
    with database.session() as session:
        session.add_all(
            [_run("queued", "queued"), _run("running", "running"), _run("done", "completed")]
        )

    assert database.mark_stale_runs_interrupted() == 2
    with database.session() as session:
        statuses = dict(session.execute(select(Run.id, Run.status)).all())
        queued = session.get(Run, "queued")
        assert queued is not None and queued.finished_at is not None
    assert statuses == {"queued": "interrupted", "running": "interrupted", "done": "completed"}
    database.close()


def test_session_rolls_back_failed_transaction():
    database = Database("sqlite:///:memory:")
    database.create_all()
    with pytest.raises(RuntimeError, match="rollback"), database.session() as session:
        session.add(_run("discarded", "queued"))
        raise RuntimeError("rollback")
    with database.session() as session:
        assert session.get(Run, "discarded") is None
