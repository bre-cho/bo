"""
capital_strategy.py
===================
Chiến lược vốn (Capital/Stake Management Strategies).

Các chiến lược:
  fixed_fractional  — Cố định % số dư (mặc định hiện tại)
  martingale        — Tăng gấp đôi stake khi thua, reset khi thắng
  anti_martingale   — Tăng stake khi thắng, giảm khi thua
  victor2           — Victor 2: 2 chuỗi, logic chéo
  victor3           — Victor 3: 3 chuỗi, logic chéo
  victor4           — Victor 4: 4 chuỗi, logic chéo
  custom            — Người dùng tự định nghĩa chuỗi

Victor Strategy Navigation:
  - Thua ở chuỗi 1 → dịch phải trong chuỗi 1
  - Thắng ở chuỗi 1 → xuống chuỗi 2 (cùng vị trí)
  - Thắng ở chuỗi N → về (1,1)
  - Thua ở chuỗi 2..N → vị trí tiếp theo trong chuỗi 1
  - Hết chuỗi 1 → về (1,1)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from typing import Optional

import redis

import config


# ──────────────────────────────────────────────────────────────────
# State dataclass
# ──────────────────────────────────────────────────────────────────

@dataclass
class CapStratState:
    strategy:       str   = "fixed_fractional"
    current_row:    int   = 0   # 0-indexed row (for Victor strategies)
    current_pos:    int   = 0   # 0-indexed position within row
    consecutive_win:int   = 0
    consecutive_loss:int  = 0
    current_stake:  float = 1.0
    base_stake:     float = 1.0  # Starting stake (for martingale reset)
    cycle_pnl:      float = 0.0  # P&L in current cycle


# ──────────────────────────────────────────────────────────────────
# Base Strategy
# ──────────────────────────────────────────────────────────────────

class BaseStrategy:
    """Abstract base for all capital strategies."""

    def __init__(self, state: CapStratState) -> None:
        self.state = state

    def next_stake(self, balance: float, signal_score: float = 60.0) -> float:
        raise NotImplementedError

    def update(self, won: bool, pnl: float) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        self.state.current_row  = 0
        self.state.current_pos  = 0
        self.state.consecutive_win  = 0
        self.state.consecutive_loss = 0
        self.state.current_stake    = self.state.base_stake
        self.state.cycle_pnl        = 0.0


# ──────────────────────────────────────────────────────────────────
# Fixed Fractional (default)
# ──────────────────────────────────────────────────────────────────

class FixedFractionalStrategy(BaseStrategy):
    """Standard Kelly-like fixed % of balance per trade."""

    def next_stake(self, balance: float, signal_score: float = 60.0) -> float:
        if signal_score >= 80:
            pct = config.STAKE_PCT_HIGH
        elif signal_score >= 60:
            pct = config.STAKE_PCT_MEDIUM
        else:
            pct = config.STAKE_PCT_LOW
        raw = balance * pct
        return float(max(config.STAKE_MIN_USD, min(config.STAKE_MAX_USD, raw)))

    def update(self, won: bool, pnl: float) -> None:
        self.state.cycle_pnl += pnl
        if won:
            self.state.consecutive_win   += 1
            self.state.consecutive_loss   = 0
        else:
            self.state.consecutive_loss  += 1
            self.state.consecutive_win    = 0


# ──────────────────────────────────────────────────────────────────
# Martingale
# ──────────────────────────────────────────────────────────────────

class MartingaleStrategy(BaseStrategy):
    """Double stake on loss, reset on win."""

    def __init__(self, state: CapStratState, multiplier: float = 2.0, max_levels: int = 6) -> None:
        super().__init__(state)
        self.multiplier = multiplier
        self.max_levels = max_levels

    def next_stake(self, balance: float, signal_score: float = 60.0) -> float:
        levels     = min(self.state.consecutive_loss, self.max_levels)
        stake      = self.state.base_stake * (self.multiplier ** levels)
        # Cap at 20% of balance
        max_stake  = balance * 0.20
        return float(max(config.STAKE_MIN_USD, min(max_stake, stake)))

    def update(self, won: bool, pnl: float) -> None:
        self.state.cycle_pnl += pnl
        if won:
            self.state.consecutive_loss = 0
            self.state.consecutive_win += 1
        else:
            self.state.consecutive_loss += 1
            self.state.consecutive_win   = 0


# ──────────────────────────────────────────────────────────────────
# Anti-Martingale
# ──────────────────────────────────────────────────────────────────

class AntiMartingaleStrategy(BaseStrategy):
    """Increase stake on win, reset on loss."""

    def __init__(self, state: CapStratState, multiplier: float = 1.5, max_levels: int = 4) -> None:
        super().__init__(state)
        self.multiplier = multiplier
        self.max_levels = max_levels

    def next_stake(self, balance: float, signal_score: float = 60.0) -> float:
        levels = min(self.state.consecutive_win, self.max_levels)
        stake  = self.state.base_stake * (self.multiplier ** levels)
        max_stake = balance * 0.10
        return float(max(config.STAKE_MIN_USD, min(max_stake, stake)))

    def update(self, won: bool, pnl: float) -> None:
        self.state.cycle_pnl += pnl
        if won:
            self.state.consecutive_win  += 1
            self.state.consecutive_loss  = 0
        else:
            self.state.consecutive_win   = 0
            self.state.consecutive_loss += 1


# ──────────────────────────────────────────────────────────────────
# Victor Strategy Base
# ──────────────────────────────────────────────────────────────────

class VictorStrategy(BaseStrategy):
    """
    Victor multi-chain capital strategy.

    Navigation rules:
      - Thua ở hàng 1       → dịch phải (pos+1), giữ hàng 1
      - Thắng ở hàng 1      → xuống hàng 2, giữ nguyên vị trí
      - Thắng ở hàng 2..N-1 → xuống hàng tiếp theo, giữ nguyên vị trí
      - Thắng ở hàng N      → reset về (0, 0)
      - Thua ở hàng 2..N    → vị trí tiếp theo ở hàng 1 (pos+1, row=0)
      - Hết hàng 1 (vượt qua độ dài) → reset về (0, 0)
    """

    def __init__(self, state: CapStratState, rows: list[list[float]], name: str) -> None:
        super().__init__(state)
        self._rows = rows
        self._name = name
        self.state.strategy = name

    def _current_stake_from_table(self) -> float:
        row = self.state.current_row
        pos = self.state.current_pos
        if row >= len(self._rows):
            return self.state.base_stake
        row_data = self._rows[row]
        if pos >= len(row_data):
            return self.state.base_stake
        return float(row_data[pos])

    def next_stake(self, balance: float, signal_score: float = 60.0) -> float:
        stake = self._current_stake_from_table() * self.state.base_stake
        # Ensure within account limits
        max_stake = balance * 0.25
        return float(max(config.STAKE_MIN_USD, min(config.STAKE_MAX_USD, min(stake, max_stake))))

    def update(self, won: bool, pnl: float) -> None:
        self.state.cycle_pnl += pnl
        n_rows = len(self._rows)

        if won:
            self.state.consecutive_win  += 1
            self.state.consecutive_loss  = 0
            if self.state.current_row == n_rows - 1:
                # Won on last row → reset to (0, 0)
                self.state.current_row = 0
                self.state.current_pos = 0
            else:
                # Move down to next row, same position
                self.state.current_row += 1
        else:
            self.state.consecutive_loss += 1
            self.state.consecutive_win   = 0
            if self.state.current_row == 0:
                # Lost on row 1 → move right
                self.state.current_pos += 1
                row_len = len(self._rows[0])
                if self.state.current_pos >= row_len:
                    # Exhausted row 1 → reset
                    self.state.current_pos = 0
            else:
                # Lost on row 2..N → go to next pos on row 1
                self.state.current_row  = 0
                self.state.current_pos += 1
                row_len = len(self._rows[0])
                if self.state.current_pos >= row_len:
                    self.state.current_pos = 0

    def status(self) -> dict:
        return {
            "strategy"  : self._name,
            "row"       : self.state.current_row + 1,
            "pos"       : self.state.current_pos + 1,
            "stake"     : self._current_stake_from_table(),
        }


# ──────────────────────────────────────────────────────────────────
# Capital Strategy Manager
# ──────────────────────────────────────────────────────────────────

_STRATEGY_FACTORIES = {
    "fixed_fractional" : FixedFractionalStrategy,
    "martingale"       : MartingaleStrategy,
    "anti_martingale"  : AntiMartingaleStrategy,
}


class CapitalStrategyManager:
    """
    Manages the active capital strategy with Redis persistence.

    Usage:
        mgr = CapitalStrategyManager()
        stake = mgr.next_stake(balance=100.0, signal_score=75.0)
        mgr.update(won=True, pnl=8.50)
        mgr.set_strategy("victor2", base_stake=1.0)
    """

    def __init__(self) -> None:
        self._r = redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        self._state    = self._load_state()
        self._strategy = self._build_strategy(self._state)

    # ── Persistence ───────────────────────────────────────────────

    def _load_state(self) -> CapStratState:
        raw = self._r.get(config.CAPITAL_STRATEGY_REDIS)
        if raw:
            try:
                d = json.loads(raw)
                return CapStratState(**{k: v for k, v in d.items() if k in CapStratState.__dataclass_fields__})
            except Exception:
                pass
        return CapStratState(strategy=config.CAPITAL_STRATEGY)

    def _save_state(self) -> None:
        self._r.set(config.CAPITAL_STRATEGY_REDIS, json.dumps(asdict(self._state)))

    # ── Build strategy object ─────────────────────────────────────

    def _build_strategy(self, state: CapStratState) -> BaseStrategy:
        name = state.strategy
        if name == "victor2":
            return VictorStrategy(state, config.VICTOR2_ROWS, "victor2")
        if name == "victor3":
            return VictorStrategy(state, config.VICTOR3_ROWS, "victor3")
        if name == "victor4":
            return VictorStrategy(state, config.VICTOR4_ROWS, "victor4")
        if name == "martingale":
            return MartingaleStrategy(state)
        if name == "anti_martingale":
            return AntiMartingaleStrategy(state)
        return FixedFractionalStrategy(state)

    # ── Public API ────────────────────────────────────────────────

    def next_stake(self, balance: float, signal_score: float = 60.0) -> float:
        """Compute next stake amount based on current strategy state."""
        return self._strategy.next_stake(balance, signal_score)

    def update(self, won: bool, pnl: float) -> None:
        """Update strategy state after trade result."""
        self._strategy.update(won, pnl)
        self._save_state()

    def set_strategy(self, name: str, base_stake: float = 1.0) -> None:
        """Switch to a different strategy (resets state)."""
        self._state              = CapStratState(strategy=name, base_stake=base_stake)
        self._state.current_stake = base_stake
        self._strategy           = self._build_strategy(self._state)
        self._save_state()
        print(f"[CapStrat] Switched to {name} (base_stake={base_stake:.2f})")

    def reset(self) -> None:
        """Reset current strategy to starting position."""
        self._strategy.reset()
        self._save_state()
        print(f"[CapStrat] Reset {self._state.strategy}")

    def status(self) -> dict:
        s = self._state
        result = {
            "strategy"       : s.strategy,
            "base_stake"     : s.base_stake,
            "consecutive_win": s.consecutive_win,
            "consecutive_loss": s.consecutive_loss,
            "cycle_pnl"      : round(s.cycle_pnl, 2),
        }
        if hasattr(self._strategy, "status"):
            result.update(self._strategy.status())
        return result


if __name__ == "__main__":
    mgr = CapitalStrategyManager()
    mgr.set_strategy("victor2", base_stake=1.0)
    for won in [False, False, True, False, True, True]:
        stake = mgr.next_stake(balance=100.0, signal_score=70.0)
        pnl   = stake * 0.85 if won else -stake
        mgr.update(won, pnl)
        print(f"  {'WIN' if won else 'LOSS'} stake={stake:.2f} status={mgr.status()}")
