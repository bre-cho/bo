# BO-MAIN Optimization & Upgrade Guide
## Áp dụng tư duy `forex-main + AI_SYSTEM_FULL` vào `bo-main` mà không viết lại code gốc

> Mục tiêu: biến `bo-main` thành **BO Trading Operating System** có validation, governance, execution control, memory, kill-switch và evolution guard.  
> Nguyên tắc: **không rewrite**, chỉ bổ sung patch theo lớp.

---

## 0. Nhận định nhanh sau khi rà repo

`bo-main` đã mạnh hơn `forex-main` ở nhiều điểm:

- Có `DecisionEngine` làm trung tâm điều phối.
- Có `RiskManager`, `ControlSystem`, `MemoryBrain`, `Pipeline`, `PermissionGate`, `LoadLimiter`.
- Có các lớp nâng cao: `sovereign_oversight`, `empire_control`, `autonomous_evolution`, `capital_strategy`, `model_registry`.
- Có API server riêng `api_server.py`.

Nhưng `bo-main` vẫn thiếu lớp **AI_SYSTEM_FULL Runtime Contract** rõ ràng như patch đã làm cho `forex-main`:

- Chưa có manifest hệ thống chuẩn để dashboard/API đọc.
- Chưa có BO-specific governance verdict độc lập trước execution.
- Chưa có contract chuẩn: `VALID / INVALID / UNCERTAIN / BLOCKED`.
- Chưa có endpoint validate signal/trade trước khi thật sự bắn lệnh.
- Chưa tách rõ `Binary Options risk logic`: payout, expiry, cooldown, stake, duplicate symbol, market session, stale candle.

Vì vậy patch đúng nhất là **bọc thêm BO Operator Runtime Layer** quanh `DecisionEngine`, không thay DecisionEngine.

---

## 1. Core Law áp dụng cho BO

```text
VALID      → ALLOW
INVALID    → BLOCK
UNCERTAIN  → SKIP
DANGER     → KILL_SWITCH
DEFAULT    → DENY
```

Với Binary Options, cần luật riêng:

```text
NO_PAYOUT_CHECK       → BLOCK
STALE_CANDLE          → BLOCK
DUPLICATE_SYMBOL      → BLOCK
LOW_WIN_PROB          → SKIP
LOW_CONFIDENCE        → SKIP
DAILY_LOSS_BREACH     → KILL_SWITCH
CONSECUTIVE_LOSS_LOCK → COOLDOWN
BROKER_UNSTABLE       → PAPER_MODE
```

---

## 2. Cấu trúc patch đề xuất

Thêm mới:

```text
bo-main/
├── operator_runtime.py          # AI_SYSTEM_FULL runtime contract
├── bo_governance.py             # BO-specific guard: payout, expiry, stale data, duplicate trade
├── operator_manifest.py         # manifest hệ thống cho API/dashboard
├── operator_api_patch.md        # hướng dẫn gắn endpoint vào api_server.py
└── docs/
    └── BO_MAIN_FROM_FOREX_MAIN_UPGRADE_DOC.md
```

Patch thủ công:

```text
api_server.py       # thêm /operator/manifest, /operator/validate
pipeline.py         # gọi BOExecutionGuard trong PermissionGate hoặc Orchestrator.dispatch
risk_manager.py     # bổ sung daily loss hard-kill + stake safety cap
config.py           # thêm config guard
```

Không đụng:

```text
brain.py
strategy.py
wave_analyzer.py
deriv_trade.py
learner.py
predictor.py
memory.py
```

---

## 3. Patch 1 — Operator Manifest

Tạo file:

```text
operator_manifest.py
```

Nội dung:

```python
OPERATOR_MANIFEST = {
    "system_id": "BO_AI_OPERATOR_SYSTEM",
    "version": "1.0.0",
    "source_inspiration": "forex-main AI_SYSTEM_FULL runtime patch",
    "core_law": {
        "VALID": "ALLOW",
        "INVALID": "BLOCK",
        "UNCERTAIN": "SKIP",
        "DANGER": "KILL_SWITCH",
        "DEFAULT": "DENY",
    },
    "execution_contract": [
        "scan_signal",
        "validate_market_data",
        "validate_signal_quality",
        "validate_predictor",
        "validate_risk",
        "validate_bo_specific_constraints",
        "permission_gate",
        "execute_or_skip",
        "record_outcome",
        "update_memory",
    ],
    "bo_specific_guards": [
        "min_payout",
        "expiry_alignment",
        "stale_candle_block",
        "duplicate_symbol_block",
        "daily_loss_kill_switch",
        "consecutive_loss_cooldown",
        "broker_health_guard",
    ],
}
```

---

## 4. Patch 2 — BO Governance Guard

Tạo file:

```text
bo_governance.py
```

Nội dung đề xuất:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import config


@dataclass
class GovernanceVerdict:
    status: str                       # VALID | INVALID | UNCERTAIN | DANGER
    action: str                       # ALLOW | BLOCK | SKIP | KILL_SWITCH
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.action == "ALLOW"


class BOExecutionGuard:
    """Binary Options execution guard.

    Lớp này không thay DecisionEngine. Nó chỉ quyết định lệnh có được phép đi qua
    trước khi gọi `place_and_wait()`.
    """

    def __init__(self) -> None:
        self._last_trade_by_symbol: dict[str, float] = {}

    def validate_trade(
        self,
        *,
        trade: Any,
        balance: float,
        daily_pnl: float,
        risk_can_trade: bool,
        broker_ok: bool = True,
        payout: Optional[float] = None,
        candle_age_seconds: Optional[float] = None,
    ) -> GovernanceVerdict:
        reasons: list[str] = []

        # DEFAULT DENY
        if trade is None:
            return GovernanceVerdict("INVALID", "BLOCK", ["trade_missing"], 0.0)

        # 1. Broker health
        if not broker_ok:
            return GovernanceVerdict("DANGER", "KILL_SWITCH", ["broker_unstable"], 0.0)

        # 2. Risk manager
        if not risk_can_trade:
            return GovernanceVerdict("DANGER", "KILL_SWITCH", ["risk_manager_blocked"], 0.0)

        # 3. Daily loss hard stop
        hard_daily_loss_pct = getattr(config, "BO_HARD_DAILY_LOSS_PCT", 0.08)
        if balance > 0 and daily_pnl <= -(balance * hard_daily_loss_pct):
            return GovernanceVerdict("DANGER", "KILL_SWITCH", ["hard_daily_loss_breach"], 0.0)

        # 4. Score threshold
        min_score = getattr(config, "BO_MIN_GOVERNANCE_SCORE", config.MIN_SIGNAL_SCORE)
        if getattr(trade, "score", 0) < min_score:
            reasons.append(f"score_too_low:{getattr(trade, 'score', 0)}<{min_score}")

        # 5. Predictor threshold
        min_wp = getattr(config, "BO_MIN_WIN_PROB", config.PREDICT_MIN_WIN_PROB)
        min_conf = getattr(config, "BO_MIN_CONFIDENCE", config.PREDICT_MIN_CONFIDENCE)
        if getattr(trade, "win_prob", 0) < min_wp:
            reasons.append(f"win_prob_too_low:{getattr(trade, 'win_prob', 0):.2f}<{min_wp:.2f}")
        if getattr(trade, "confidence", 0) < min_conf:
            reasons.append(f"confidence_too_low:{getattr(trade, 'confidence', 0):.2f}<{min_conf:.2f}")

        # 6. Payout check — rất quan trọng với BO
        min_payout = getattr(config, "BO_MIN_PAYOUT", 0.75)
        if payout is not None and payout < min_payout:
            reasons.append(f"payout_too_low:{payout:.2f}<{min_payout:.2f}")

        # 7. Stale candle check
        max_age = getattr(config, "BO_MAX_CANDLE_AGE_SECONDS", 90)
        if candle_age_seconds is not None and candle_age_seconds > max_age:
            reasons.append(f"stale_candle:{candle_age_seconds:.0f}s>{max_age}s")

        # 8. Duplicate symbol gap
        now = datetime.now(timezone.utc).timestamp()
        min_gap = getattr(config, "BO_MIN_SAME_SYMBOL_GAP_SECONDS", 180)
        symbol = getattr(trade, "symbol", "UNKNOWN")
        last_ts = self._last_trade_by_symbol.get(symbol)
        if last_ts and now - last_ts < min_gap:
            reasons.append(f"duplicate_symbol_gap:{now-last_ts:.0f}s<{min_gap}s")

        if reasons:
            return GovernanceVerdict("UNCERTAIN", "SKIP", reasons, 0.0)

        self._last_trade_by_symbol[symbol] = now
        return GovernanceVerdict("VALID", "ALLOW", ["all_governance_checks_passed"], 1.0)
```

---

## 5. Patch 3 — Runtime Contract

Tạo file:

```text
operator_runtime.py
```

Nội dung:

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from bo_governance import BOExecutionGuard, GovernanceVerdict


class BOOperatorRuntime:
    """AI_SYSTEM_FULL style runtime cho BO.

    Nhiệm vụ: nhận context, trả verdict thống nhất để API/dashboard/engine dùng chung.
    """

    def __init__(self) -> None:
        self.guard = BOExecutionGuard()

    def validate_execution_context(self, context: dict[str, Any]) -> dict[str, Any]:
        verdict: GovernanceVerdict = self.guard.validate_trade(
            trade=context.get("trade"),
            balance=float(context.get("balance", 0.0)),
            daily_pnl=float(context.get("daily_pnl", 0.0)),
            risk_can_trade=bool(context.get("risk_can_trade", False)),
            broker_ok=bool(context.get("broker_ok", True)),
            payout=context.get("payout"),
            candle_age_seconds=context.get("candle_age_seconds"),
        )
        return asdict(verdict)
```

---

## 6. Patch 4 — Gắn vào `pipeline.py`

Vị trí phù hợp nhất: `Orchestrator.dispatch()` ngay sau khi `PermissionGate` pass và trước `executor_fn(trade)`.

Tìm đoạn:

```python
print(
    f"  [Gate] ✅ Thông qua {len(passed)}/{len(passed)+len(blocked)} cổng "
    f"({', '.join(passed)})"
)

execute_start = time.time()
raw_result    = executor_fn(trade)
```

Thay bằng:

```python
print(
    f"  [Gate] ✅ Thông qua {len(passed)}/{len(passed)+len(blocked)} cổng "
    f"({', '.join(passed)})"
)

# BO Governance Guard — AI_SYSTEM_FULL runtime protection
try:
    from bo_governance import BOExecutionGuard
    bo_guard = BOExecutionGuard()
    verdict = bo_guard.validate_trade(
        trade=trade,
        balance=balance,
        daily_pnl=0.0,  # optional: inject từ RiskManager ở patch nâng cao
        risk_can_trade=risk_can_trade,
        broker_ok=True,
    )
    if not verdict.allowed:
        print(f"  [BOGuard] 🚫 {verdict.action}: {', '.join(verdict.reasons)}")
        self._metrics.record_rejection(f"bo_guard:{verdict.action}")
        return TradeOutcome(
            symbol      = trade.symbol,
            direction   = trade.direction,
            score       = trade.score,
            won         = False,
            pnl         = 0.0,
            stake       = trade.stake,
            executed_at = time.time(),
            latency_ms  = 0.0,
            rejected_by = "bo_guard:" + ",".join(verdict.reasons),
        )
except Exception as exc:
    print(f"  [BOGuard] ⚠️ guard failed closed: {exc}")
    return None

execute_start = time.time()
raw_result    = executor_fn(trade)
```

> Bản tốt hơn: inject `BOExecutionGuard` vào `Orchestrator.__init__` để không tạo lại mỗi dispatch. Nhưng patch trên là cách ít đụng code nhất.

---

## 7. Patch 5 — Gắn vào `api_server.py`

Trong `create_app()`, thêm endpoint sau phần `/status` hoặc gần nhóm control.

```python
    @app.get("/operator/manifest")
    async def operator_manifest():
        from operator_manifest import OPERATOR_MANIFEST
        return OPERATOR_MANIFEST

    @app.post("/operator/validate")
    async def operator_validate(payload: dict):
        from operator_runtime import BOOperatorRuntime
        runtime = BOOperatorRuntime()
        return runtime.validate_execution_context(payload)
```

Verify:

```bash
curl http://localhost:8000/operator/manifest
```

Kỳ vọng:

```json
{
  "system_id": "BO_AI_OPERATOR_SYSTEM",
  "core_law": {
    "VALID": "ALLOW",
    "INVALID": "BLOCK",
    "UNCERTAIN": "SKIP",
    "DANGER": "KILL_SWITCH",
    "DEFAULT": "DENY"
  }
}
```

---

## 8. Patch 6 — Thêm config guard

Thêm vào cuối `config.py`:

```python
# ──────────────────────────────────────────────────────────────────
# BO AI SYSTEM FULL GOVERNANCE PATCH
# ──────────────────────────────────────────────────────────────────

BO_MIN_GOVERNANCE_SCORE = 65
BO_MIN_WIN_PROB = 0.58
BO_MIN_CONFIDENCE = 0.55
BO_MIN_PAYOUT = 0.75
BO_MAX_CANDLE_AGE_SECONDS = 90
BO_MIN_SAME_SYMBOL_GAP_SECONDS = 180
BO_HARD_DAILY_LOSS_PCT = 0.08
BO_GOVERNANCE_FAIL_CLOSED = True
```

---

## 9. Điểm khác biệt quan trọng giữa Forex và BO

| Layer | Forex-main | BO-main cần đổi |
|---|---|---|
| Entry | price-based entry | expiry-based entry |
| Risk | stop-loss / take-profit | stake + payout + expiry |
| Execution | open/close position | contract resolves by time |
| Guard quan trọng | spread, slippage, broker | payout, stale candle, duplicate symbol |
| Memory | signal → PnL | signal + expiry + payout → win/loss |
| Kill-switch | drawdown | drawdown + loss streak + broker instability |

Vì vậy không bê nguyên Forex guard sang BO. Chỉ dùng tư duy runtime/governance, còn luật phải BO-specific.

---

## 10. Verify checklist

### 10.1 Compile

```bash
python -m compileall .
```

### 10.2 API boot

```bash
uvicorn api_server:create_app --factory --host 0.0.0.0 --port 8000
```

### 10.3 Manifest test

```bash
curl http://localhost:8000/operator/manifest
```

### 10.4 Validate test — block missing trade

```bash
curl -X POST http://localhost:8000/operator/validate \
  -H "Content-Type: application/json" \
  -d '{"balance":100,"daily_pnl":0,"risk_can_trade":true}'
```

Expected:

```json
{"status":"INVALID","action":"BLOCK"}
```

### 10.5 Validate test — kill-switch risk

```bash
curl -X POST http://localhost:8000/operator/validate \
  -H "Content-Type: application/json" \
  -d '{"trade":{"symbol":"R_100","score":80,"win_prob":0.7,"confidence":0.7},"balance":100,"daily_pnl":-10,"risk_can_trade":true}'
```

Expected:

```json
{"status":"DANGER","action":"KILL_SWITCH"}
```

---

## 11. Patch order an toàn

```text
1. Add operator_manifest.py
2. Add bo_governance.py
3. Add operator_runtime.py
4. Add config constants
5. Add API endpoints
6. Compile
7. Test API manifest/validate
8. Patch pipeline.py guard trước executor
9. Run PAPER mode only
10. Sau 50-100 paper trades mới bật LIVE
```

---

## 12. Patch tiếp theo mạnh nhất

Sau patch này, bước mạnh nhất nên làm là:

```text
BO PAYOUT-AWARE DECISION ENGINE PATCH
```

Lý do: BO thắng/thua không chỉ phụ thuộc signal, mà phụ thuộc payout. Một signal win_prob 58% với payout 0.95 có thể đáng trade hơn signal 62% với payout 0.70.

Công thức decision nên chuyển từ:

```text
score + win_prob + confidence
```

sang:

```text
expected_value = win_prob * payout - (1 - win_prob)
```

Luật:

```text
EV <= 0      → SKIP
EV thấp      → PAPER_ONLY
EV cao       → ALLOW
EV cao + confidence cao + memory pass → PRIORITY_QUEUE
```

---

## 13. Kết luận

`bo-main` không cần rewrite. Repo đã có nền khá mạnh. Việc cần làm là gắn thêm lớp:

```text
AI_SYSTEM_FULL Runtime Contract
+ BO Governance Guard
+ Operator Manifest/API
+ Payout-aware Decision Gate
```

Sau nâng cấp, hệ sẽ chuyển từ:

```text
autonomous BO robot
```

sang:

```text
BO Trading Operating System có kiểm soát rủi ro, memory, governance và kill-switch.
```
