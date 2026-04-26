from __future__ import annotations

import copy
import random
from statistics import mean
from typing import Any, Dict

from .governance import EvolutionGovernance
from .models import PolicyGenome
from .storage import BrainStore


class FullEvolutionEngine:
    def __init__(self, store: BrainStore, governance: EvolutionGovernance) -> None:
        self.store = store
        self.gov = governance

    def load_policy(self) -> PolicyGenome:
        data = self.store.get_json("active_policy", default=None)
        if data:
            return PolicyGenome.from_dict(data)
        policy = PolicyGenome().clamp()
        self.store.set_json("active_policy", policy.to_dict())
        self.store.set_json("last_safe_policy", policy.to_dict())
        return policy

    def save_policy(self, policy: PolicyGenome, safe: bool = False) -> None:
        self.store.set_json("active_policy", policy.clamp().to_dict())
        if safe:
            self.store.set_json("last_safe_policy", policy.to_dict())

    def rollback_last_safe(self) -> PolicyGenome:
        data = self.store.get_json("last_safe_policy", default=None)
        policy = PolicyGenome.from_dict(data) if data else PolicyGenome()
        self.save_policy(policy, safe=True)
        return policy

    def evaluate_recent(self, limit: int = 200) -> Dict[str, Any]:
        rows = self.store.read_jsonl("trade_events", limit=limit)
        if not rows:
            return {"n": 0, "win_rate": 0.0, "pnl": 0.0, "avg_score": 0.0}
        wins = sum(1 for row in rows if row.get("won"))
        pnl = sum(float(row.get("pnl", 0.0)) for row in rows)
        scores = [float(row.get("final_score", 0.0)) for row in rows]
        return {"n": len(rows), "win_rate": wins / len(rows), "pnl": pnl, "avg_score": mean(scores) if scores else 0.0}

    def propose_mutation(self, base: PolicyGenome, metrics: Dict[str, Any]) -> PolicyGenome:
        new = copy.deepcopy(base)
        new.parent_policy_id = base.policy_id
        new.policy_id = "policy_mut_" + str(random.randint(100000, 999999))
        win_rate = float(metrics.get("win_rate", 0.0))
        pnl = float(metrics.get("pnl", 0.0))
        n_trades = int(metrics.get("n", 0))
        step = base.mutation_rate
        if n_trades < 50:
            new.min_final_score += 2.0
            new.max_stake_multiplier = min(new.max_stake_multiplier, 1.0)
        elif win_rate >= 0.58 and pnl > 0:
            new.min_final_score -= 1.0 * step * 10
            new.max_stake_multiplier += 0.03
            new.min_win_prob -= 0.005
        else:
            new.min_final_score += 1.5 * step * 10
            new.max_stake_multiplier -= 0.05
            new.min_win_prob += 0.01
            new.min_confidence += 0.01
        new.promoted = False
        return new.clamp()

    def evolve_once(self) -> Dict[str, Any]:
        active = self.load_policy()
        if self.gov.kill_switch_active():
            safe = self.rollback_last_safe()
            return {"status": "rollback", "reason": "kill_switch_active", "active_policy": safe.to_dict()}
        metrics = self.evaluate_recent()
        candidate = self.propose_mutation(active, metrics)
        ok, reasons = self.gov.validate_policy_change(active, candidate)
        if not ok:
            return {"status": "blocked", "reasons": reasons, "metrics": metrics, "active_policy": active.to_dict()}
        self.store.set_json("candidate_policy", candidate.to_dict())
        return {"status": "candidate_created", "metrics": metrics, "candidate_policy": candidate.to_dict()}

    def promote_candidate(self, min_trades: int = 100, min_win_rate: float = 0.56, min_pnl: float = 0.0) -> Dict[str, Any]:
        candidate_data = self.store.get_json("candidate_policy", default=None)
        if not candidate_data:
            return {"status": "no_candidate"}
        metrics = self.evaluate_recent(limit=300)
        if metrics["n"] >= min_trades and metrics["win_rate"] >= min_win_rate and metrics["pnl"] >= min_pnl:
            policy = PolicyGenome.from_dict(candidate_data)
            policy.promoted = True
            self.save_policy(policy, safe=True)
            return {"status": "promoted", "metrics": metrics, "active_policy": policy.to_dict()}
        return {"status": "not_promoted", "metrics": metrics, "candidate_policy": candidate_data}