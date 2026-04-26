from types import SimpleNamespace

import bo_governance
import decision_engine
from pipeline import LoadLimiter, Orchestrator, PermissionGate, PipelineMetrics, QueuedTrade, TradeQueue


class _FakePrediction:
    def __init__(self) -> None:
        self.should_trade = True
        self.win_prob = 0.72
        self.confidence = 0.81
        self.stake_suggestion = 1.75


class _FakeMemory:
    def __init__(self) -> None:
        self.consult_calls = []
        self.record_calls = []

    def consult(self, features):
        self.consult_calls.append(features)
        return SimpleNamespace(
            hard_block=False,
            reason="ok",
            priority_boost=0.0,
            win_rate=0.6,
            sample_count=10,
        )

    def record_outcome(self, features, won, pnl):
        self.record_calls.append((features, won, pnl))


class _FakeRisk:
    def __init__(self) -> None:
        self.state = SimpleNamespace(daily_pnl=3.25)
        self.updated = []

    def can_trade(self, balance=0.0, direction="both"):
        return True, "OK"

    def compute_stake(self, signal_score, balance):
        return 1.0

    def update_after_trade(self, won, pnl):
        self.updated.append((won, pnl))


class _FakeLogger:
    def __init__(self) -> None:
        self.records = []

    def log(self, record):
        self.records.append(record)


class _FakeAIBrain:
    def __init__(self) -> None:
        self.decide_calls = []
        self.record_calls = []

    def decide(self, ctx):
        self.decide_calls.append(ctx)
        return SimpleNamespace(
            action="ALLOW",
            final_score=79.5,
            reasons=["valid_edge"],
            stake_multiplier=1.0,
        )

    def record_outcome(self, ctx, decision, won, pnl, stake):
        self.record_calls.append((ctx, decision, won, pnl, stake))


class _MemoryFacade:
    @staticmethod
    def features_from_signal(signal):
        return {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "score": signal.score,
        }


def _make_engine(memory=None, risk=None):
    engine = decision_engine.DecisionEngine.__new__(decision_engine.DecisionEngine)
    engine._active_symbols = ["R_100"]
    engine._consecutive_errors = 0
    engine._pipeline = Orchestrator(
        queue=TradeQueue(),
        gate=PermissionGate(),
        limiter=LoadLimiter(),
        metrics=PipelineMetrics(),
    )
    engine.cap_strat = None
    engine.learner = SimpleNamespace(get_params=lambda: SimpleNamespace(stake_multiplier=1.0))
    engine.risk = risk or _FakeRisk()
    engine.memory = memory or _FakeMemory()
    engine.ai_brain = _FakeAIBrain()
    engine.logger = _FakeLogger()
    return engine


def _make_signal():
    return SimpleNamespace(
        symbol="R_100",
        direction="CALL",
        score=84.0,
        rsi=53.0,
        momentum=0.12,
        macd_hist=0.004,
        bb_position=0.58,
        indicators={"fib_zone": "F618", "correction": True},
        wave=None,
    )


def test_run_live_cycle_end_to_end_chain(monkeypatch):
    engine = _make_engine()
    signal = _make_signal()

    monkeypatch.setattr(decision_engine, "MemoryBrain", _MemoryFacade)
    monkeypatch.setattr(decision_engine, "pick_best_entry", lambda symbols: signal)
    monkeypatch.setattr(decision_engine.deriv_data, "fetch_candles", lambda symbol: object())
    monkeypatch.setattr(engine, "decide_entry", lambda sig, df, bal: (True, _FakePrediction()))
    monkeypatch.setattr(
        decision_engine,
        "place_and_wait",
        lambda contract_type, symbol, stake: {
            "won": True,
            "pnl": 0.87,
            "payout": 1.87,
            "contract_id": "CID-1",
        },
    )

    guard_calls = []

    class _AllowGuard:
        def validate_trade(self, **kwargs):
            guard_calls.append(kwargs)
            return bo_governance.GovernanceVerdict(
                status="VALID",
                action="ALLOW",
                reasons=["ok"],
                score=1.0,
            )

    monkeypatch.setattr(bo_governance, "BOExecutionGuard", _AllowGuard)

    engine.run_live_cycle(balance=100.0)

    metrics = engine._pipeline._metrics.snapshot()
    assert metrics["total_submitted"] == 1
    assert metrics["total_executed"] == 1

    assert len(guard_calls) == 1
    assert guard_calls[0]["daily_pnl"] == engine.risk.state.daily_pnl

    assert len(engine.logger.records) == 1
    logged = engine.logger.records[0]
    assert logged.symbol == "R_100"
    assert isinstance(logged.indicators, dict)

    # Memory is consulted pre-queue and again at Gate 4, then updated after execution.
    assert len(engine.memory.consult_calls) >= 2
    assert len(engine.memory.record_calls) == 1

    assert len(engine.ai_brain.decide_calls) == 1
    assert len(engine.ai_brain.record_calls) == 1
    assert len(engine.risk.updated) == 1


def test_run_live_cycle_blocks_on_bo_guard_fail_closed(monkeypatch):
    risk = _FakeRisk()
    risk.state.daily_pnl = -999.0
    engine = _make_engine(risk=risk)
    signal = _make_signal()

    monkeypatch.setattr(decision_engine, "MemoryBrain", _MemoryFacade)
    monkeypatch.setattr(decision_engine, "pick_best_entry", lambda symbols: signal)
    monkeypatch.setattr(decision_engine.deriv_data, "fetch_candles", lambda symbol: object())
    monkeypatch.setattr(engine, "decide_entry", lambda sig, df, bal: (True, _FakePrediction()))
    monkeypatch.setattr(
        decision_engine,
        "place_and_wait",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("place_and_wait must not be called")),
    )

    guard_calls = []

    class _BlockGuard:
        def validate_trade(self, **kwargs):
            guard_calls.append(kwargs)
            return bo_governance.GovernanceVerdict(
                status="DANGER",
                action="KILL_SWITCH",
                reasons=["hard_daily_loss_breach"],
                score=0.0,
            )

    monkeypatch.setattr(bo_governance, "BOExecutionGuard", _BlockGuard)

    engine.run_live_cycle(balance=100.0)

    metrics = engine._pipeline._metrics.snapshot()
    assert metrics["total_submitted"] == 1
    assert metrics["total_executed"] == 0
    assert metrics["total_rejected"] >= 1

    assert len(guard_calls) == 1
    assert guard_calls[0]["daily_pnl"] == -999.0

    assert len(engine.logger.records) == 0
    assert len(engine.memory.record_calls) == 0
    assert len(engine.ai_brain.decide_calls) == 1
    assert len(engine.ai_brain.record_calls) == 0
    assert len(engine.risk.updated) == 0


def test_run_live_cycle_skips_when_predictor_rejects(monkeypatch):
    engine = _make_engine()
    signal = _make_signal()

    monkeypatch.setattr(decision_engine, "MemoryBrain", _MemoryFacade)
    monkeypatch.setattr(decision_engine, "pick_best_entry", lambda symbols: signal)
    monkeypatch.setattr(decision_engine.deriv_data, "fetch_candles", lambda symbol: object())
    monkeypatch.setattr(engine, "decide_entry", lambda sig, df, bal: (False, _FakePrediction()))
    monkeypatch.setattr(
        decision_engine,
        "place_and_wait",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("place_and_wait must not be called")),
    )

    class _AllowGuard:
        def validate_trade(self, **kwargs):
            return bo_governance.GovernanceVerdict(
                status="VALID",
                action="ALLOW",
                reasons=["ok"],
                score=1.0,
            )

    monkeypatch.setattr(bo_governance, "BOExecutionGuard", _AllowGuard)

    engine.run_live_cycle(balance=100.0)

    metrics = engine._pipeline._metrics.snapshot()
    assert metrics["total_submitted"] == 0
    assert metrics["total_executed"] == 0
    assert len(engine.logger.records) == 0
    assert len(engine.memory.record_calls) == 0
    assert len(engine.ai_brain.decide_calls) == 0
    assert len(engine.ai_brain.record_calls) == 0
    assert len(engine.risk.updated) == 0


def test_run_live_cycle_stops_on_memory_hard_block(monkeypatch):
    class _HardBlockMemory(_FakeMemory):
        def consult(self, features):
            self.consult_calls.append(features)
            return SimpleNamespace(
                hard_block=True,
                reason="hard block",
                priority_boost=0.0,
                win_rate=0.2,
                sample_count=20,
            )

    memory = _HardBlockMemory()
    engine = _make_engine(memory=memory)
    signal = _make_signal()

    monkeypatch.setattr(decision_engine, "MemoryBrain", _MemoryFacade)
    monkeypatch.setattr(decision_engine, "pick_best_entry", lambda symbols: signal)
    monkeypatch.setattr(decision_engine.deriv_data, "fetch_candles", lambda symbol: object())
    monkeypatch.setattr(
        decision_engine,
        "place_and_wait",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("place_and_wait must not be called")),
    )

    class _AllowGuard:
        def validate_trade(self, **kwargs):
            return bo_governance.GovernanceVerdict(
                status="VALID",
                action="ALLOW",
                reasons=["ok"],
                score=1.0,
            )

    monkeypatch.setattr(bo_governance, "BOExecutionGuard", _AllowGuard)

    engine.run_live_cycle(balance=100.0)

    metrics = engine._pipeline._metrics.snapshot()
    assert metrics["total_submitted"] == 0
    assert metrics["total_executed"] == 0
    assert len(memory.consult_calls) == 1
    assert len(memory.record_calls) == 0
    assert len(engine.logger.records) == 0
    assert len(engine.ai_brain.decide_calls) == 0
    assert len(engine.ai_brain.record_calls) == 0
    assert len(engine.risk.updated) == 0


def test_dispatch_rejects_when_risk_false_and_other_gates_not_enough():
    queue = TradeQueue()
    gate = PermissionGate()
    limiter = LoadLimiter()
    metrics = PipelineMetrics()
    orch = Orchestrator(queue=queue, gate=gate, limiter=limiter, metrics=metrics)

    trade = QueuedTrade(
        priority=1.0,
        enqueued_at=0.0,
        symbol="R_100",
        direction="CALL",
        score=10.0,
        win_prob=0.10,
        confidence=0.10,
        stake=1.0,
        wave_active=False,
        fib_zone="NONE",
        signal_ref=None,
        trade_features=None,
    )
    assert orch.submit(trade)

    called = {"executor": 0}

    def _executor(_trade):
        called["executor"] += 1
        return {"won": True, "pnl": 0.0}

    outcome = orch.dispatch(
        balance=100.0,
        risk_can_trade=False,
        daily_pnl=0.0,
        executor_fn=_executor,
        memory_brain=None,
    )

    assert outcome is not None
    assert outcome.rejected_by
    assert "risk" in outcome.rejected_by
    assert called["executor"] == 0
