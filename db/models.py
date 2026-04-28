"""
db/models.py
============
SQLAlchemy ORM models for persistent audit/history storage.

Tables
------
trade_logs      — every completed trade (mirrored from Redis log)
audit_logs      — every mutating API call (control / engine / strategy)
model_versions  — model registry snapshots
evolution_runs  — evolution cycle champion results
"""
from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── Trade log ─────────────────────────────────────────────────────

class TradeLog(Base):
    __tablename__ = "trade_logs"

    id          : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at  : Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    symbol      : Mapped[str]           = mapped_column(String(32), nullable=False, index=True)
    direction   : Mapped[str]           = mapped_column(String(8),  nullable=False)   # CALL | PUT
    stake_usd   : Mapped[float]         = mapped_column(Float,      nullable=False)
    payout_usd  : Mapped[float | None]  = mapped_column(Float,      nullable=True)
    result      : Mapped[str | None]    = mapped_column(String(8),  nullable=True)    # WIN | LOSS | OPEN
    score       : Mapped[float | None]  = mapped_column(Float,      nullable=True)
    strategy    : Mapped[str | None]    = mapped_column(String(64), nullable=True)
    contract_id : Mapped[str | None]    = mapped_column(String(64), nullable=True, unique=True)
    raw_json    : Mapped[str | None]    = mapped_column(Text,       nullable=True)    # original redis entry

    def __repr__(self) -> str:
        return f"<TradeLog id={self.id} {self.symbol} {self.direction} {self.result}>"


# ── Audit log ─────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id          : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at  : Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    endpoint    : Mapped[str]           = mapped_column(String(128), nullable=False, index=True)
    method      : Mapped[str]           = mapped_column(String(8),   nullable=False)
    status_code : Mapped[int | None]    = mapped_column(Integer,     nullable=True)
    api_key_hint: Mapped[str | None]    = mapped_column(String(16),  nullable=True)   # first 4 chars only
    payload_json: Mapped[str | None]    = mapped_column(Text,        nullable=True)
    ip_address  : Mapped[str | None]    = mapped_column(String(64),  nullable=True)
    duration_ms : Mapped[float | None]  = mapped_column(Float,       nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} {self.method} {self.endpoint} {self.status_code}>"


# ── Model versions ────────────────────────────────────────────────

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id          : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at  : Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    model_name  : Mapped[str]           = mapped_column(String(64),  nullable=False, index=True)
    version_tag : Mapped[str]           = mapped_column(String(64),  nullable=False)
    accuracy    : Mapped[float | None]  = mapped_column(Float,       nullable=True)
    win_rate    : Mapped[float | None]  = mapped_column(Float,       nullable=True)
    is_active   : Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=False)
    metrics_json: Mapped[str | None]    = mapped_column(Text,        nullable=True)
    file_path   : Mapped[str | None]    = mapped_column(String(256), nullable=True)

    def __repr__(self) -> str:
        return f"<ModelVersion {self.model_name}@{self.version_tag} active={self.is_active}>"


# ── Evolution runs ────────────────────────────────────────────────

class EvolutionRun(Base):
    __tablename__ = "evolution_runs"

    id              : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at      : Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    genome_id       : Mapped[str]           = mapped_column(String(64),  nullable=False)
    generation      : Mapped[int]           = mapped_column(Integer,     nullable=False)
    fitness         : Mapped[float]         = mapped_column(Float,       nullable=False)
    win_rate_pct    : Mapped[float | None]  = mapped_column(Float,       nullable=True)
    profit_factor   : Mapped[float | None]  = mapped_column(Float,       nullable=True)
    n_trades        : Mapped[int | None]    = mapped_column(Integer,     nullable=True)
    promoted        : Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=False)
    genes_json      : Mapped[str | None]    = mapped_column(Text,        nullable=True)

    def __repr__(self) -> str:
        return f"<EvolutionRun genome={self.genome_id} gen={self.generation} fit={self.fitness:.4f}>"
