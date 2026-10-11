"""SQLAlchemy models for reproducible evaluation runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by all application tables."""


class Run(Base):
    """One requested evaluation run."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    app_version: Mapped[str] = mapped_column(String(32))
    litellm_host: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    models: Mapped[list[RunModel]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    tasks: Mapped[list[RunTask]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    results: Mapped[list[Result]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    aggregates: Mapped[list[Aggregate]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class RunModel(Base):
    """A model selected for a run."""

    __tablename__ = "run_models"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    pricing_source_summary: Mapped[str | None] = mapped_column(String(32))

    run: Mapped[Run] = relationship(back_populates="models")


class RunTask(Base):
    """A versioned task snapshot selected for a run."""

    __tablename__ = "run_tasks"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    task_version: Mapped[str] = mapped_column(String(50))
    task_file_sha256: Mapped[str] = mapped_column(String(64))
    gold_file_sha256: Mapped[str] = mapped_column(String(64))
    prompt_sha256: Mapped[str] = mapped_column(String(64))

    run: Mapped[Run] = relationship(back_populates="tasks")


class Result(Base):
    """One model/task/item/repetition outcome."""

    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "model_id",
            "task_id",
            "item_id",
            "repetition",
            name="uq_result_job",
        ),
        Index("ix_results_run_model_task", "run_id", "model_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(500))
    task_id: Mapped[str] = mapped_column(String(100))
    item_id: Mapped[str] = mapped_column(String(200))
    repetition: Mapped[int] = mapped_column(Integer)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    gold_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_rendered: Mapped[dict[str, str] | None] = mapped_column(JSON)
    raw_output: Mapped[str | None] = mapped_column(Text)
    parsed_output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    derived_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    comparison_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    matched: Mapped[bool | None] = mapped_column(Boolean)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_source: Mapped[str] = mapped_column(String(32), default="unknown")
    latency_ms: Mapped[float | None] = mapped_column(Float)
    total_elapsed_ms: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="results")


class Aggregate(Base):
    """A cached aggregate statistic, recomputable from results."""

    __tablename__ = "aggregates"
    __table_args__ = (Index("ix_aggregates_run_model_task", "run_id", "model_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(500))
    task_id: Mapped[str] = mapped_column(String(100))
    stat_name: Mapped[str] = mapped_column(String(100))
    field: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[float | None] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer)

    run: Mapped[Run] = relationship(back_populates="aggregates")
