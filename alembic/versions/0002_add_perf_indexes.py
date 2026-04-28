"""Add performance indexes for dashboard/audit high-load queries

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # audit_logs: common query is endpoint prefix + newest first + status
    op.create_index("ix_audit_logs_method_created_at", "audit_logs", ["method", "created_at"])
    op.create_index("ix_audit_logs_status_created_at", "audit_logs", ["status_code", "created_at"])

    # trade_logs: common query is symbol/result over time windows
    op.create_index("ix_trade_logs_symbol_created_at", "trade_logs", ["symbol", "created_at"])
    op.create_index("ix_trade_logs_result_created_at", "trade_logs", ["result", "created_at"])

    # model_versions: dashboard commonly needs latest active version per model
    op.create_index("ix_model_versions_name_active_created", "model_versions", ["model_name", "is_active", "created_at"])

    # evolution_runs: newest by genome/generation and promoted filter
    op.create_index("ix_evolution_runs_genome_created", "evolution_runs", ["genome_id", "created_at"])
    op.create_index("ix_evolution_runs_promoted_created", "evolution_runs", ["promoted", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_evolution_runs_promoted_created", table_name="evolution_runs")
    op.drop_index("ix_evolution_runs_genome_created", table_name="evolution_runs")
    op.drop_index("ix_model_versions_name_active_created", table_name="model_versions")
    op.drop_index("ix_trade_logs_result_created_at", table_name="trade_logs")
    op.drop_index("ix_trade_logs_symbol_created_at", table_name="trade_logs")
    op.drop_index("ix_audit_logs_status_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_method_created_at", table_name="audit_logs")
