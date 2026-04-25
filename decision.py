from __future__ import annotations

import math
import uuid
from typing import List, Tuple

from .models import BrainContext, BrainDecision, PolicyGenome

class FullDecisionEngine:
    """Final authority layer before trade execution.

    BO-specific: optimizes for probability edge and payout/expiry discipline, not forex RR.
    """
    def __init__(self, policy: PolicyGenome) -> None:
        self.policy = policy.clamp()

    def _regime_score(self, regime: str) -> float:
        regime = (regime or "UNKNOWN").upper()
        if regime in {"TREND", "MOMENTUM", "BREAKOUT"}:
            return 0.70
        if regime in {"RANGE", "MEAN_REVERSION"}:
            return 0.58
        if regime in {"NEWS", "SPIKE", "CHAOS", "LOW_LIQUIDITY"}:
            return 0.10
        return 0.45

    def _memory_score(self, ctx: BrainContext) -> float:
        if ctx.memory_sample_size <= 0:
            return 0.50
        confidence = min(1.0, ctx.memory_sample_size / max(1, self.policy.memory_min_samples * 2))
        return 0.50 * (1 - confidence) + ctx.memory_win_rate * confidence

    def decide(self, ctx: BrainContext) -> BrainDecision:
        reasons: List[str] = []
        if ctx.memory_hard_block:
            return BrainDecision(uuid.uuid4().hex, "BLOCK", 0.0, 0.0, ["memory_hard_block"], self.policy.policy_id)
        if not ctx.risk_allowed:
            return BrainDecision(uuid.uuid4().hex, "BLOCK", 0.0, 0.0, [f"risk_block:{ctx.risk_reason}"], self.policy.policy_id)
        if ctx.win_prob < self.policy.min_win_prob:
            reasons.append(f"weak_win_prob:{ctx.win_prob:.2f}<{self.policy.min_win_prob:.2f}")
        if ctx.confidence < self.policy.min_confidence:
            reasons.append(f"weak_confidence:{ctx.confidence:.2f}<{self.policy.min_confidence:.2f}")

        signal_component = max(0.0, min(1.0, ctx.signal_score / 100.0))
        predictor_component = max(0.0, min(1.0, ctx.win_prob)) * max(0.2, min(1.0, ctx.confidence + 0.25))
        memory_component = self._memory_score(ctx)
        regime_component = self._regime_score(ctx.market_regime)

        composite = (
            signal_component * self.policy.signal_weight
            + predictor_component * self.policy.predictor_weight
            + memory_component * self.policy.memory_weight
            + regime_component * self.policy.regime_weight
        )
        final_score = round(composite * 100.0, 2)

        # BO payout edge: required win probability rises when payout is weak.
        if ctx.payout and ctx.payout > 0:
            breakeven = 1.0 / (1.0 + ctx.payout)
            if ctx.win_prob <= breakeven + 0.03:
                reasons.append(f"no_bo_edge:wp={ctx.win_prob:.2f} breakeven={breakeven:.2f}")

        if final_score < self.policy.min_final_score:
            reasons.append(f"low_final_score:{final_score:.1f}<{self.policy.min_final_score:.1f}")

        if reasons:
            action = "SKIP" if not any(r.startswith("no_bo_edge") for r in reasons) else "BLOCK"
            stake_multiplier = 0.0
        else:
            action = "ALLOW"
            # Increase only modestly; survival > aggression.
            edge = max(0.0, final_score - self.policy.min_final_score) / 100.0
            stake_multiplier = min(self.policy.max_stake_multiplier, 1.0 + edge)
            reasons.append("valid_edge")

        return BrainDecision(
            decision_id=uuid.uuid4().hex,
            action=action,
            final_score=final_score,
            stake_multiplier=round(stake_multiplier, 4),
            reasons=reasons,
            policy_id=self.policy.policy_id,
        )
