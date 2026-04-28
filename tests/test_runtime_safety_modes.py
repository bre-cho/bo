import decision_engine
import deriv_trade


class _FakeRedis:
    def __init__(self, value=None):
        self._value = value

    def get(self, _key):
        return self._value


def test_engine_default_mode_from_env_paper(monkeypatch):
    monkeypatch.setenv("ENGINE_MODE", "PAPER")
    engine = decision_engine.DecisionEngine.__new__(decision_engine.DecisionEngine)
    engine._r = _FakeRedis(value=None)

    mode = engine._load_mode()
    assert mode == decision_engine.SystemMode.PAPER


def test_engine_default_mode_invalid_fallbacks_to_paper(monkeypatch):
    monkeypatch.setenv("ENGINE_MODE", "NOT_A_MODE")
    engine = decision_engine.DecisionEngine.__new__(decision_engine.DecisionEngine)
    engine._r = _FakeRedis(value=None)

    mode = engine._load_mode()
    assert mode == decision_engine.SystemMode.PAPER


def test_place_and_wait_blocked_when_trade_mode_not_live(monkeypatch):
    monkeypatch.setenv("TRADE_MODE", "PAPER")

    res = deriv_trade.place_and_wait("CALL", "R_100", 1.0)

    assert res["status"] == "blocked"
    assert res["reason"] == "TRADE_MODE is not LIVE"
    assert res["contract_id"] == "PAPER_BLOCKED"


def test_place_and_wait_runs_when_trade_mode_live(monkeypatch):
    monkeypatch.setenv("TRADE_MODE", "LIVE")

    def _fake_run(coro):
        coro.close()
        return {
            "contract_id": "CID_TEST",
            "won": True,
            "buy_price": 1.0,
            "sell_price": 1.8,
            "payout": 1.8,
            "pnl": 0.8,
            "status": "won",
        }

    monkeypatch.setattr(deriv_trade.asyncio, "run", _fake_run)

    invalidate_called = {"n": 0}

    def _invalidate():
        invalidate_called["n"] += 1

    monkeypatch.setattr(deriv_trade, "invalidate_balance_cache", _invalidate)

    res = deriv_trade.place_and_wait("CALL", "R_100", 1.0)

    assert res["contract_id"] == "CID_TEST"
    assert res["status"] == "won"
    assert invalidate_called["n"] == 1
