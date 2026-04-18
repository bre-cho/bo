"""
control_system.py
=================
Control System — Điều khiển vận hành theo chính sách.

Chức năng:
  1. Daily Take-Profit (TP): Tự dừng khi lãi ngày >= ngưỡng
  2. Daily Stop-Loss (SL): Tự dừng khi lỗ ngày >= ngưỡng
  3. Wave Direction Filter: Chỉ CALL / chỉ PUT / cả hai
  4. User Restart Gate: Sau khi dừng do TP/SL → chờ user restart

State lưu trong Redis để bền vững qua restart.
User phải gọi reset_daily_stop() để khởi động lại sau khi bị dừng.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime

import redis

import config


@dataclass
class ControlState:
    # Daily TP/SL configuration (settable at runtime)
    daily_take_profit_usd: float = 0.0   # 0 = disabled
    daily_stop_loss_usd:   float = 0.0   # 0 = disabled

    # Wave direction filter: "both" | "up_only" | "down_only"
    wave_direction_filter: str = "both"

    # Runtime stop state
    stopped_by_tpsl: bool  = False
    stop_reason:     str   = ""
    stop_date:       str   = ""   # ISO date when stopped

    # Today's TP/SL reference (reset daily)
    ref_date:        str   = ""
    start_balance:   float = 0.0  # Balance at start of day (for % based SL)


_REDIS_CONTROL_KEY = "Deriv_ControlState"


class ControlSystem:
    """
    Quản lý chính sách vận hành của robot.

    Usage:
        ctrl = ControlSystem()

        # Check before trading
        allowed, reason = ctrl.can_trade(daily_pnl=15.0, direction="CALL")
        if not allowed:
            print(f"Blocked: {reason}")

        # After trade
        ctrl.update_daily_pnl(new_pnl=17.5)

        # User restart (after TP/SL stop)
        ctrl.reset_daily_stop()

        # Configure
        ctrl.set_daily_tp(50.0)
        ctrl.set_daily_sl(30.0)
        ctrl.set_wave_filter("up_only")
    """

    def __init__(self) -> None:
        self._r    = redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        self.state = self._load()
        self._init_defaults()

    # ── Persistence ───────────────────────────────────────────────

    def _load(self) -> ControlState:
        raw = self._r.get(_REDIS_CONTROL_KEY)
        if raw:
            try:
                d = json.loads(raw)
                return ControlState(**{k: v for k, v in d.items()
                                       if k in ControlState.__dataclass_fields__})
            except Exception:
                pass
        return ControlState()

    def _save(self) -> None:
        self._r.set(_REDIS_CONTROL_KEY, json.dumps(asdict(self.state)))

    def _init_defaults(self) -> None:
        """Apply config defaults if not previously set."""
        if self.state.daily_take_profit_usd == 0.0 and config.DAILY_TAKE_PROFIT_USD > 0:
            self.state.daily_take_profit_usd = config.DAILY_TAKE_PROFIT_USD
        if self.state.daily_stop_loss_usd == 0.0 and config.DAILY_STOP_LOSS_USD > 0:
            self.state.daily_stop_loss_usd = config.DAILY_STOP_LOSS_USD
        if self.state.wave_direction_filter == "both":
            self.state.wave_direction_filter = config.WAVE_DIRECTION_FILTER
        self._reset_if_new_day()
        self._save()

    def _reset_if_new_day(self) -> None:
        today = date.today().isoformat()
        if self.state.ref_date != today:
            # New day → clear stopped state
            self.state.ref_date       = today
            self.state.stopped_by_tpsl = False
            self.state.stop_reason    = ""
            self.state.stop_date      = ""
            self._save()
            print(f"[ControlSystem] New day ({today}) — TP/SL stop cleared.")

    # ── Core gate ─────────────────────────────────────────────────

    def can_trade(self, daily_pnl: float, direction: str = "both") -> tuple[bool, str]:
        """
        Check if trading is allowed under current control policy.

        Parameters
        ----------
        daily_pnl : Today's P&L (positive = profit, negative = loss)
        direction : Signal direction "CALL" or "PUT"

        Returns (allowed: bool, reason: str)
        """
        self._reset_if_new_day()

        # 1. Already stopped by TP/SL today
        if self.state.stopped_by_tpsl:
            return False, f"Đã dừng: {self.state.stop_reason} (khởi động lại thủ công)"

        # 2. Daily Take-Profit check
        tp = self.state.daily_take_profit_usd
        if tp > 0 and daily_pnl >= tp:
            self._trigger_stop(f"Đạt chốt lãi ngày: +{daily_pnl:.2f} USD >= +{tp:.2f} USD")
            return False, self.state.stop_reason

        # 3. Daily Stop-Loss check
        sl = self.state.daily_stop_loss_usd
        if sl > 0 and daily_pnl <= -sl:
            self._trigger_stop(f"Đạt chốt lỗ ngày: {daily_pnl:.2f} USD <= -{sl:.2f} USD")
            return False, self.state.stop_reason

        # 4. Wave direction filter
        if direction != "both":
            wf = self.state.wave_direction_filter
            if wf == "up_only" and direction == "PUT":
                return False, "Wave filter: chỉ bắt sóng TĂNG (CALL)"
            if wf == "down_only" and direction == "CALL":
                return False, "Wave filter: chỉ bắt sóng GIẢM (PUT)"

        return True, "OK"

    def _trigger_stop(self, reason: str) -> None:
        self.state.stopped_by_tpsl = True
        self.state.stop_reason     = reason
        self.state.stop_date       = datetime.now().isoformat()
        self._save()
        print(f"[ControlSystem] 🛑 AUTO STOP — {reason}")

    # ── User control ──────────────────────────────────────────────

    def reset_daily_stop(self) -> None:
        """
        User manually restarts after TP/SL stop.
        Also resets TP/SL reference — effectively starting a new 'session'.
        """
        self.state.stopped_by_tpsl = False
        self.state.stop_reason     = ""
        self.state.stop_date       = ""
        self._save()
        print("[ControlSystem] ▶️  Đã khởi động lại — tiếp tục giao dịch.")

    def set_daily_tp(self, amount_usd: float) -> None:
        """Set daily take-profit threshold in USD (0 = disable)."""
        self.state.daily_take_profit_usd = max(0.0, amount_usd)
        self._save()
        status = f"+{amount_usd:.2f} USD" if amount_usd > 0 else "tắt"
        print(f"[ControlSystem] Chốt lãi ngày: {status}")

    def set_daily_sl(self, amount_usd: float) -> None:
        """Set daily stop-loss threshold in USD (0 = disable)."""
        self.state.daily_stop_loss_usd = max(0.0, amount_usd)
        self._save()
        status = f"-{amount_usd:.2f} USD" if amount_usd > 0 else "tắt"
        print(f"[ControlSystem] Chốt lỗ ngày: {status}")

    def set_wave_filter(self, mode: str) -> None:
        """Set wave direction filter: 'both' | 'up_only' | 'down_only'"""
        allowed = {"both", "up_only", "down_only"}
        if mode not in allowed:
            raise ValueError(f"Wave filter must be one of {allowed}")
        self.state.wave_direction_filter = mode
        self._save()
        print(f"[ControlSystem] Wave filter: {mode}")

    # ── Status ────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "daily_take_profit_usd" : self.state.daily_take_profit_usd,
            "daily_stop_loss_usd"   : self.state.daily_stop_loss_usd,
            "wave_direction_filter" : self.state.wave_direction_filter,
            "stopped_by_tpsl"       : self.state.stopped_by_tpsl,
            "stop_reason"           : self.state.stop_reason,
            "stop_date"             : self.state.stop_date,
        }

    def summary(self) -> str:
        s = self.state
        tp  = f"+{s.daily_take_profit_usd:.2f}" if s.daily_take_profit_usd > 0 else "off"
        sl  = f"-{s.daily_stop_loss_usd:.2f}"   if s.daily_stop_loss_usd   > 0 else "off"
        wf  = {"both": "↕ Cả hai", "up_only": "↑ Chỉ tăng", "down_only": "↓ Chỉ giảm"}.get(
            s.wave_direction_filter, s.wave_direction_filter
        )
        stop = f"🛑 STOPPED: {s.stop_reason}" if s.stopped_by_tpsl else "▶️  Running"
        return (
            f"Control | TP={tp}  SL={sl}  Wave={wf}  {stop}"
        )


if __name__ == "__main__":
    ctrl = ControlSystem()
    ctrl.set_daily_tp(50.0)
    ctrl.set_daily_sl(30.0)
    ctrl.set_wave_filter("both")

    print(ctrl.can_trade(daily_pnl=15.0, direction="CALL"))
    print(ctrl.can_trade(daily_pnl=51.0, direction="CALL"))
    ctrl.reset_daily_stop()
    print(ctrl.status())
