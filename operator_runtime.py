from __future__ import annotations

from dataclasses import asdict
from typing import Any

from bo_governance import BOExecutionGuard, GovernanceVerdict


class BOOperatorRuntime:
    """AI_SYSTEM_FULL style runtime cho BO.

    Nhiệm vụ: nhận context, trả verdict thống nhất để API/dashboard/engine dùng chung.
    """

    def __init__(self) -> None:
        self.guard = BOExecutionGuard()

    def validate_execution_context(self, context: dict[str, Any]) -> dict[str, Any]:
        verdict: GovernanceVerdict = self.guard.validate_trade(
            trade=context.get("trade"),
            balance=float(context.get("balance", 0.0)),
            daily_pnl=float(context.get("daily_pnl", 0.0)),
            risk_can_trade=bool(context.get("risk_can_trade", False)),
            broker_ok=bool(context.get("broker_ok", True)),
            payout=context.get("payout"),
            candle_age_seconds=context.get("candle_age_seconds"),
        )
        return asdict(verdict)
