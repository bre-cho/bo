from __future__ import annotations

from typing import Any, Dict, Optional

from .decision import FullDecisionEngine
from .evolution import FullEvolutionEngine
from .governance import EvolutionGovernance
from .memory import FullMemoryEngine
from .models import BrainContext, BrainDecision, TradeMemoryEvent
from .storage import BrainStore

class AITradingBrain:
    """Facade: Decision + Memory + Evolution + Governance.

    Integration points:
      before queue/execution: brain.decide(ctx)
      after result: brain.record_outcome(ctx, decision, won, pnl, stake)
      scheduled: brain.evolve_once() / brain.promote_candidate()
    """
    def __init__(self, redis_client: Any = None, data_dir: str = "models/ai_trading_brain") -> None:
        self.store = BrainStore(redis_client=redis_client, data_dir=data_dir)
        self.governance = EvolutionGovernance(self.store)
        self.evolution = FullEvolutionEngine(self.store, self.governance)
        self.policy = self.evolution.load_policy()
        self.memory = FullMemoryEngine(
            self.store,
            min_samples=self.policy.memory_min_samples,
            hard_block_loss_rate=self.policy.memory_block_loss_rate,
        )
        self.decision = FullDecisionEngine(self.policy)

    def decide(self, ctx: BrainContext) -> BrainDecision:
        if self.governance.kill_switch_active():
            return BrainDecision(
                decision_id="kill_switch",
                action="BLOCK",
                final_score=0.0,
                stake_multiplier=0.0,
                reasons=["kill_switch_active"],
                policy_id=self.policy.policy_id,
            )
        ctx = self.memory.enrich_context(ctx)
        d = self.decision.decide(ctx)
        self.store.lpush_json("decisions", {"context": ctx.__dict__, "decision": d.to_dict()}, trim=5000)
        return d

    def record_outcome(self, ctx: BrainContext, decision: BrainDecision, won: bool, pnl: float, stake: float) -> None:
        features = dict(ctx.meta)
        features.update({
            "symbol": ctx.symbol,
            "direction": ctx.direction,
            "signal_score": ctx.signal_score,
            "win_prob": ctx.win_prob,
            "confidence": ctx.confidence,
            "fib_zone": ctx.fib_zone,
            "wave_active": ctx.wave_active,
            "market_regime": ctx.market_regime,
            "expiry_seconds": ctx.expiry_seconds,
        })
        event = TradeMemoryEvent(
            decision_id=decision.decision_id,
            symbol=ctx.symbol,
            direction=ctx.direction,
            won=won,
            pnl=pnl,
            stake=stake,
            final_score=decision.final_score,
            policy_id=decision.policy_id,
            features=features,
        )
        self.memory.record(event)

    def evolve_once(self) -> Dict[str, Any]:
        return self.evolution.evolve_once()

    def promote_candidate(self) -> Dict[str, Any]:
        return self.evolution.promote_candidate()

    def status(self) -> Dict[str, Any]:
        return {
            "active_policy": self.policy.to_dict(),
            "kill_switch": self.store.get_json("kill_switch", default={"active": False}),
            "recent_metrics": self.evolution.evaluate_recent(),
            "candidate_policy": self.store.get_json("candidate_policy", default=None),
        }
