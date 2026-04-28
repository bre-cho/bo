from types import SimpleNamespace

import bo_governance
import config


def _trade_ok():
    return SimpleNamespace(
        symbol="R_100",
        score=95.0,
        win_prob=0.9,
        confidence=0.9,
    )


def test_guard_kill_switch_when_live_trading_disabled(monkeypatch):
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(config, "DERIV_ENV", "live")
    monkeypatch.setattr(config, "DERIV_API_TOKEN", "token_live_ok")

    guard = bo_governance.BOExecutionGuard()
    verdict = guard.validate_trade(
        trade=_trade_ok(),
        balance=100.0,
        daily_pnl=0.0,
        risk_can_trade=True,
        broker_ok=True,
        payout=0.9,
        candle_age_seconds=10,
    )

    assert verdict.action == "KILL_SWITCH"
    assert "live_trading_disabled" in verdict.reasons


def test_guard_kill_switch_when_deriv_env_not_live(monkeypatch):
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", True)
    monkeypatch.setattr(config, "DERIV_ENV", "demo")
    monkeypatch.setattr(config, "DERIV_API_TOKEN", "token_demo")

    guard = bo_governance.BOExecutionGuard()
    verdict = guard.validate_trade(
        trade=_trade_ok(),
        balance=100.0,
        daily_pnl=0.0,
        risk_can_trade=True,
        broker_ok=True,
        payout=0.9,
        candle_age_seconds=10,
    )

    assert verdict.action == "KILL_SWITCH"
    assert "deriv_env_not_live" in verdict.reasons
