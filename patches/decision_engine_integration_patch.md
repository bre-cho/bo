# PATCH: gắn FULL AI TRADING BRAIN vào `decision_engine.py`

## 1) Import thêm ở đầu file

```python
from ai_trading_brain import AITradingBrain, BrainContext
```

## 2) Trong `DecisionEngine.__init__`, sau khi tạo `self.memory = MemoryBrain()` thêm:

```python
# FULL AI TRADING BRAIN — Decision + Memory + Evolution + Governance
self.ai_brain = AITradingBrain(redis_client=self._r)
```

## 3) Trong `run_live_cycle`, sau khi có `win_prob`, `confidence`, `wave_active`, `fib_zone`, trước khi tạo `priority`, thêm:

```python
brain_ctx = BrainContext(
    symbol=best.symbol,
    direction=best.direction,
    signal_score=float(best.score),
    win_prob=float(win_prob),
    confidence=float(confidence),
    stake=float(stake),
    balance=float(balance),
    fib_zone=fib_zone,
    wave_active=wave_active,
    payout=getattr(config, "BO_DEFAULT_PAYOUT", 0.87),
    expiry_seconds=getattr(config, "BO_DEFAULT_EXPIRY_SECONDS", 60),
    market_regime=getattr(best, "market_regime", "UNKNOWN"),
    risk_allowed=allowed,
    risk_reason="OK",
)
brain_decision = self.ai_brain.decide(brain_ctx)

if brain_decision.action == "BLOCK":
    print(f"  [AIBrain] ❌ BLOCK {brain_decision.final_score} {brain_decision.reasons}")
    return

if brain_decision.action == "SKIP":
    print(f"  [AIBrain] ⚠️ SKIP {brain_decision.final_score} {brain_decision.reasons}")
    return

stake = round(stake * brain_decision.stake_multiplier, 2)
```

## 4) Khi tạo `QueuedTrade`, nếu không muốn sửa dataclass, gắn thêm metadata vào `signal_ref`:

```python
setattr(best, "ai_brain_ctx", brain_ctx)
setattr(best, "ai_brain_decision", brain_decision)
```

Đặt 2 dòng này ngay trước `queued = QueuedTrade(...)`.

## 5) Trong `_execute_trade`, sau khi biết `won`, `pnl`, `payout`, thêm trước `return result`:

```python
try:
    brain_ctx = getattr(signal, "ai_brain_ctx", None)
    brain_decision = getattr(signal, "ai_brain_decision", None)
    if brain_ctx is not None and brain_decision is not None:
        self.ai_brain.record_outcome(
            ctx=brain_ctx,
            decision=brain_decision,
            won=won,
            pnl=pnl,
            stake=trade.stake,
        )
except Exception as exc:
    print(f"  [AIBrain] record_outcome failed: {exc}")
```

## 6) Trong chu kỳ learning/evolution

Có thể gọi sau `trigger_learning()` hoặc mỗi N chu kỳ:

```python
if self._cycle_count > 0 and self._cycle_count % 200 == 0:
    print("  [AIBrain] Evolution cycle...")
    print(self.ai_brain.evolve_once())
```

## 7) FastAPI nếu repo dùng `api_server.py`

Trong `api_server.py`, thêm:

```python
from ai_trading_brain.operator_api import router as ai_trading_brain_router
app.include_router(ai_trading_brain_router)
```

Endpoint mới:

- `GET /v1/ai-trading-brain/status`
- `POST /v1/ai-trading-brain/evolve`
- `POST /v1/ai-trading-brain/promote`
- `POST /v1/ai-trading-brain/kill-switch?reason=manual`
- `DELETE /v1/ai-trading-brain/kill-switch`
