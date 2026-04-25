from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .models import PolicyGenome
from .storage import BrainStore

class EvolutionGovernance:
    """Prevents runaway mutation. Evolution can suggest; governance decides."""
    def __init__(self, store: BrainStore) -> None:
        self.store = store

    def validate_policy_change(self, old: PolicyGenome, new: PolicyGenome) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        checks = {
            "min_final_score": abs(new.min_final_score - old.min_final_score) / max(1.0, old.min_final_score),
            "min_win_prob": abs(new.min_win_prob - old.min_win_prob) / max(0.01, old.min_win_prob),
            "min_confidence": abs(new.min_confidence - old.min_confidence) / max(0.01, old.min_confidence),
            "max_stake_multiplier": abs(new.max_stake_multiplier - old.max_stake_multiplier) / max(0.01, old.max_stake_multiplier),
        }
        for name, delta in checks.items():
            if delta > old.max_daily_policy_change:
                reasons.append(f"runaway_delta:{name}:{delta:.2f}")
        if new.max_stake_multiplier > 1.5:
            reasons.append("unsafe_stake_multiplier")
        if new.min_final_score < 45:
            reasons.append("unsafe_min_score")
        return len(reasons) == 0, reasons

    def kill_switch_active(self) -> bool:
        state = self.store.get_json("kill_switch", default={}) or {}
        return bool(state.get("active", False))

    def activate_kill_switch(self, reason: str) -> None:
        self.store.set_json("kill_switch", {"active": True, "reason": reason})

    def deactivate_kill_switch(self) -> None:
        self.store.set_json("kill_switch", {"active": False, "reason": ""})
