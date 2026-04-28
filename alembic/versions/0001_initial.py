"""Initial schema — trade_logs, audit_logs, model_versions, evolution_runs

Revision ID: 0001
Revises: 
Create Date: 2026-04-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trade_logs",
        sa.Column("id",          sa.Integer(),    nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("symbol",      sa.String(32),  nullable=False),
        sa.Column("direction",   sa.String(8),   nullable=False),
        sa.Column("stake_usd",   sa.Float(),     nullable=False),
        sa.Column("payout_usd",  sa.Float(),     nullable=True),
        sa.Column("result",      sa.String(8),   nullable=True),
        sa.Column("score",       sa.Float(),     nullable=True),
        sa.Column("strategy",    sa.String(64),  nullable=True),
        sa.Column("contract_id", sa.String(64),  nullable=True),
        sa.Column("raw_json",    sa.Text(),      nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id"),
    )
    op.create_index("ix_trade_logs_created_at", "trade_logs", ["created_at"])
    op.create_index("ix_trade_logs_symbol",     "trade_logs", ["symbol"])

    op.create_table(
        "audit_logs",
        sa.Column("id",           sa.Integer(),   nullable=False),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("endpoint",     sa.String(128), nullable=False),
        sa.Column("method",       sa.String(8),   nullable=False),
        sa.Column("status_code",  sa.Integer(),   nullable=True),
        sa.Column("api_key_hint", sa.String(16),  nullable=True),
        sa.Column("payload_json", sa.Text(),      nullable=True),
        sa.Column("ip_address",   sa.String(64),  nullable=True),
        sa.Column("duration_ms",  sa.Float(),     nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_endpoint",   "audit_logs", ["endpoint"])

    op.create_table(
        "model_versions",
        sa.Column("id",           sa.Integer(),    nullable=False),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("model_name",   sa.String(64),   nullable=False),
        sa.Column("version_tag",  sa.String(64),   nullable=False),
        sa.Column("accuracy",     sa.Float(),      nullable=True),
        sa.Column("win_rate",     sa.Float(),      nullable=True),
        sa.Column("is_active",    sa.Boolean(),    nullable=False),
        sa.Column("metrics_json", sa.Text(),       nullable=True),
        sa.Column("file_path",    sa.String(256),  nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_versions_created_at",  "model_versions", ["created_at"])
    op.create_index("ix_model_versions_model_name",  "model_versions", ["model_name"])

    op.create_table(
        "evolution_runs",
        sa.Column("id",            sa.Integer(),  nullable=False),
        sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("genome_id",     sa.String(64), nullable=False),
        sa.Column("generation",    sa.Integer(),  nullable=False),
        sa.Column("fitness",       sa.Float(),    nullable=False),
        sa.Column("win_rate_pct",  sa.Float(),    nullable=True),
        sa.Column("profit_factor", sa.Float(),    nullable=True),
        sa.Column("n_trades",      sa.Integer(),  nullable=True),
        sa.Column("promoted",      sa.Boolean(),  nullable=False),
        sa.Column("genes_json",    sa.Text(),     nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evolution_runs_created_at", "evolution_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("evolution_runs")
    op.drop_table("model_versions")
    op.drop_table("audit_logs")
    op.drop_table("trade_logs")
