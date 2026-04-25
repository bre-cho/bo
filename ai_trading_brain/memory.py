from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Tuple

from .models import BrainContext, TradeMemoryEvent
from .storage import BrainStore

class FullMemoryEngine:
    def __init__(self, store: BrainStore, min_samples: int = 12, hard_block_loss_rate: float = 0.68) -> None:
        self.store = store
        self.min_samples = min_samples
        self.hard_block_loss_rate = hard_block_loss_rate

    def fingerprint(self, ctx: BrainContext) -> str:
        score_band = int(ctx.signal_score // 10) * 10
        return ":".join([
            ctx.symbol,
            ctx.direction,
            str(score_band),
            ctx.fib_zone or "NONE",
            "W1" if ctx.wave_active else "W0",
            ctx.market_regime or "UNKNOWN",
        ])

    def summarize(self, fingerprint: str) -> Dict[str, Any]:
        table = self.store.get_json("memory_summary", default={}) or {}
        return table.get(fingerprint, {"wins": 0, "losses": 0, "pnl": 0.0, "n": 0})

    def enrich_context(self, ctx: BrainContext) -> BrainContext:
        fp = self.fingerprint(ctx)
        stat = self.summarize(fp)
        n = int(stat.get("n", 0))
        wins = int(stat.get("wins", 0))
        losses = int(stat.get("losses", 0))
        ctx.memory_sample_size = n
        ctx.memory_win_rate = wins / n if n else 0.5
        loss_rate = losses / n if n else 0.0
        ctx.memory_hard_block = bool(n >= self.min_samples and loss_rate >= self.hard_block_loss_rate)
        ctx.meta["memory_fingerprint"] = fp
        ctx.meta["memory_loss_rate"] = round(loss_rate, 4)
        return ctx

    def record(self, event: TradeMemoryEvent) -> None:
        self.store.lpush_json("trade_events", event.to_dict(), trim=10000)
        fp = event.features.get("memory_fingerprint") or self._fingerprint_from_features(event.features)
        table = self.store.get_json("memory_summary", default={}) or {}
        stat = table.get(fp, {"wins": 0, "losses": 0, "pnl": 0.0, "n": 0})
        stat["n"] = int(stat.get("n", 0)) + 1
        stat["wins"] = int(stat.get("wins", 0)) + (1 if event.won else 0)
        stat["losses"] = int(stat.get("losses", 0)) + (0 if event.won else 1)
        stat["pnl"] = round(float(stat.get("pnl", 0.0)) + float(event.pnl), 6)
        table[fp] = stat
        self.store.set_json("memory_summary", table)

    def _fingerprint_from_features(self, f: Dict[str, Any]) -> str:
        return ":".join([
            str(f.get("symbol", "UNKNOWN")),
            str(f.get("direction", "UNKNOWN")),
            str(int(float(f.get("signal_score", 0)) // 10) * 10),
            str(f.get("fib_zone", "NONE")),
            "W1" if f.get("wave_active") else "W0",
            str(f.get("market_regime", "UNKNOWN")),
        ])
