from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

CORE_LAW = {
    "VALID": "ALLOW",
    "INVALID": "BLOCK",
    "UNCERTAIN": "SKIP",
    "DEFAULT": "DENY",
}

@dataclass
class BrainContext:
    symbol: str
    direction: str
    signal_score: float
    win_prob: float = 0.5
    confidence: float = 0.3
    stake: float = 0.0
    balance: float = 0.0
    fib_zone: str = "NONE"
    wave_active: bool = False
    payout: float = 0.0
    expiry_seconds: int = 60
    market_regime: str = "UNKNOWN"
    memory_win_rate: float = 0.5
    memory_sample_size: int = 0
    memory_hard_block: bool = False
    risk_allowed: bool = True
    risk_reason: str = "OK"
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class BrainDecision:
    decision_id: str
    action: str                  # ALLOW / SKIP / BLOCK / PAPER_ONLY
    final_score: float
    stake_multiplier: float
    reasons: List[str]
    policy_id: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class TradeMemoryEvent:
    decision_id: str
    symbol: str
    direction: str
    won: bool
    pnl: float
    stake: float
    final_score: float
    policy_id: str
    features: Dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class PolicyGenome:
    policy_id: str = field(default_factory=lambda: "policy_" + uuid.uuid4().hex[:10])
    min_final_score: float = 62.0
    min_win_prob: float = 0.56
    min_confidence: float = 0.38
    memory_block_loss_rate: float = 0.68
    memory_min_samples: int = 12
    max_stake_multiplier: float = 1.15
    signal_weight: float = 0.40
    predictor_weight: float = 0.28
    memory_weight: float = 0.20
    regime_weight: float = 0.12
    mutation_rate: float = 0.06
    canary_ratio: float = 0.10
    max_daily_policy_change: float = 0.12
    promoted: bool = False
    parent_policy_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def clamp(self) -> "PolicyGenome":
        self.min_final_score = min(85.0, max(45.0, self.min_final_score))
        self.min_win_prob = min(0.75, max(0.45, self.min_win_prob))
        self.min_confidence = min(0.75, max(0.20, self.min_confidence))
        self.memory_block_loss_rate = min(0.90, max(0.50, self.memory_block_loss_rate))
        self.memory_min_samples = int(min(100, max(5, self.memory_min_samples)))
        self.max_stake_multiplier = min(1.50, max(0.40, self.max_stake_multiplier))
        self.mutation_rate = min(0.15, max(0.01, self.mutation_rate))
        self.canary_ratio = min(0.25, max(0.02, self.canary_ratio))
        total = self.signal_weight + self.predictor_weight + self.memory_weight + self.regime_weight
        if total <= 0:
            self.signal_weight, self.predictor_weight, self.memory_weight, self.regime_weight = 0.4, 0.28, 0.2, 0.12
        else:
            self.signal_weight /= total
            self.predictor_weight /= total
            self.memory_weight /= total
            self.regime_weight /= total
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PolicyGenome":
        fields = PolicyGenome.__dataclass_fields__.keys()
        return PolicyGenome(**{k: v for k, v in data.items() if k in fields}).clamp()
