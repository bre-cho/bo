from ai_trading_brain import AITradingBrain, BrainContext


def test_allow_skip_record_evolve_smoke(tmp_path):
    brain = AITradingBrain(data_dir=str(tmp_path))
    ctx = BrainContext(
        symbol="R_100",
        direction="CALL",
        signal_score=82,
        win_prob=0.64,
        confidence=0.62,
        payout=0.87,
        risk_allowed=True,
        market_regime="TREND",
    )
    decision = brain.decide(ctx)
    assert decision.action in {"ALLOW", "SKIP", "BLOCK"}
    brain.record_outcome(ctx, decision, won=True, pnl=0.87, stake=1.0)
    status = brain.status()
    assert "active_policy" in status
    evo = brain.evolve_once()
    assert evo["status"] in {"candidate_created", "blocked", "rollback"}
