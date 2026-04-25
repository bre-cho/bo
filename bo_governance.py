from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import config


@dataclass
class GovernanceVerdict:
    status: str                       # VALID | INVALID | UNCERTAIN | DANGER
    action: str                       # ALLOW | BLOCK | SKIP | KILL_SWITCH
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.action == "ALLOW"


class BOExecutionGuard:
    """Binary Options execution guard.

    Lớp này không thay DecisionEngine. Nó chỉ quyết định lệnh có được phép đi qua
    trước khi gọi `place_and_wait()`.
    """

    def __init__(self) -> None:
        self._last_trade_by_symbol: dict[str, float] = {}

    def validate_trade(
        self,
        *,
        trade: Any,
        balance: float,
        daily_pnl: float,
        risk_can_trade: bool,
        broker_ok: bool = True,
        payout: Optional[float] = None,
        candle_age_seconds: Optional[float] = None,
    ) -> GovernanceVerdict:
        reasons: list[str] = []

        # DEFAULT DENY
        if trade is None:
            return GovernanceVerdict("INVALID", "BLOCK", ["trade_missing"], 0.0)

        # 1. Broker health
        if not broker_ok:
            return GovernanceVerdict("DANGER", "KILL_SWITCH", ["broker_unstable"], 0.0)

        # 2. Risk manager
        if not risk_can_trade:
            return GovernanceVerdict("DANGER", "KILL_SWITCH", ["risk_manager_blocked"], 0.0)

        # 3. Daily loss hard stop
        hard_daily_loss_pct = getattr(config, "BO_HARD_DAILY_LOSS_PCT", 0.08)
        if balance > 0 and daily_pnl <= -(balance * hard_daily_loss_pct):
            return GovernanceVerdict("DANGER", "KILL_SWITCH", ["hard_daily_loss_breach"], 0.0)

        # 4. Score threshold
        min_score = getattr(config, "BO_MIN_GOVERNANCE_SCORE", getattr(config, "MIN_SIGNAL_SCORE", 60))
        if getattr(trade, "score", 0) < min_score:
            reasons.append(f"score_too_low:{getattr(trade, 'score', 0)}<{min_score}")

        # 5. Predictor threshold
        min_wp = getattr(config, "BO_MIN_WIN_PROB", getattr(config, "PREDICT_MIN_WIN_PROB", 0.55))
        min_conf = getattr(config, "BO_MIN_CONFIDENCE", getattr(config, "PREDICT_MIN_CONFIDENCE", 0.55))
        if getattr(trade, "win_prob", 0) < min_wp:
            reasons.append(f"win_prob_too_low:{getattr(trade, 'win_prob', 0):.2f}<{min_wp:.2f}")
        if getattr(trade, "confidence", 0) < min_conf:
            reasons.append(f"confidence_too_low:{getattr(trade, 'confidence', 0):.2f}<{min_conf:.2f}")

        # 6. Payout check — rất quan trọng với BO
        min_payout = getattr(config, "BO_MIN_PAYOUT", 0.75)
        if payout is not None and payout < min_payout:
            reasons.append(f"payout_too_low:{payout:.2f}<{min_payout:.2f}")

        # 7. Stale candle check
        max_age = getattr(config, "BO_MAX_CANDLE_AGE_SECONDS", 90)
        if candle_age_seconds is not None and candle_age_seconds > max_age:
            reasons.append(f"stale_candle:{candle_age_seconds:.0f}s>{max_age}s")

        # 8. Duplicate symbol gap
        now = datetime.now(timezone.utc).timestamp()
        min_gap = getattr(config, "BO_MIN_SAME_SYMBOL_GAP_SECONDS", 180)
        symbol = getattr(trade, "symbol", "UNKNOWN")
        last_ts = self._last_trade_by_symbol.get(symbol)
        if last_ts and now - last_ts < min_gap:
            reasons.append(f"duplicate_symbol_gap:{now-last_ts:.0f}s<{min_gap}s")

        if reasons:
            return GovernanceVerdict("UNCERTAIN", "SKIP", reasons, 0.0)

        self._last_trade_by_symbol[symbol] = now
        return GovernanceVerdict("VALID", "ALLOW", ["all_governance_checks_passed"], 1.0)
