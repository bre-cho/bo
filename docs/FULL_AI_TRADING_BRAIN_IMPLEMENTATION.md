# FULL AI TRADING BRAIN — Decision + Memory + Evolution Engine

## Mục tiêu

Nâng `bo-main` từ bot có nhiều module rời rạc thành **AI Trading Operating System có kiểm soát**:

```text
SIGNAL
→ Decision Brain
→ Memory Brain
→ Risk/Governance Guard
→ Queue/Execution
→ Outcome Memory
→ Evolution Candidate
→ Canary/Promotion/Rollback
```

Core Law:

```text
VALID    → ALLOW
INVALID  → BLOCK
UNCERTAIN→ SKIP
DEFAULT  → DENY
```

## Những gì patch bổ sung

```text
ai_trading_brain/
├── models.py          # dataclass chuẩn hóa context/decision/policy/event
├── storage.py         # Redis-first + file fallback
├── decision.py        # chấm điểm lệnh cuối cùng trước execution
├── memory.py          # ghi outcome + fingerprint + hard block
├── governance.py      # runaway detector + kill-switch
├── evolution.py       # tạo candidate policy + promote + rollback
├── brain.py           # facade để gắn vào repo cũ
└── operator_api.py    # FastAPI control endpoints
```

## Tư duy khác biệt với bot thường

Bot thường:

```text
indicator → signal → trade
```

AI Trading Brain:

```text
indicator → signal → context → decision → memory consult → governance → trade/skip/block
```

Hệ không “tự học vô hạn”. Hệ chỉ tự tạo **candidate policy** trong giới hạn an toàn. Muốn policy thành production phải qua promotion gate.

## Decision Engine

Input chính:

- `signal_score`
- `win_prob`
- `confidence`
- `memory_win_rate`
- `risk_allowed`
- `market_regime`
- `payout`
- `expiry_seconds`

Output:

- `ALLOW`: cho phép trade
- `SKIP`: chưa đủ chắc, không trade
- `BLOCK`: cấm trade vì vi phạm risk/memory/payout edge

BO-specific edge:

```text
breakeven = 1 / (1 + payout)
```

Nếu `win_prob` không cao hơn breakeven đủ biên an toàn → BLOCK.

## Memory Engine

Mỗi lệnh tạo fingerprint:

```text
symbol:direction:score_band:fib_zone:wave_active:market_regime
```

Memory lưu:

- wins
- losses
- pnl
- sample size
- loss rate

Khi fingerprint có đủ mẫu và loss rate cao → `memory_hard_block = True`.

## Evolution Engine

Không sửa strategy trực tiếp. Quy trình đúng:

```text
active_policy
→ evaluate recent outcomes
→ propose candidate_policy
→ governance check
→ canary/paper validation
→ promote_candidate
→ last_safe_policy
```

Nếu có kill-switch:

```text
rollback_last_safe_policy
```

## Governance Guard

Chặn:

- policy thay đổi quá mạnh trong một lần
- tăng stake multiplier nguy hiểm
- giảm ngưỡng score quá thấp
- kill-switch đang active

## Cách apply nhanh

Copy folder:

```bash
cp -r ai_trading_brain /path/to/bo-main/
```

Sau đó apply hướng dẫn trong:

```text
patches/decision_engine_integration_patch.md
```

## Verify

```bash
cd /path/to/bo-main
python -m py_compile ai_trading_brain/*.py
pytest tests/test_ai_trading_brain_smoke.py -q
```

Nếu chưa có pytest:

```bash
python - <<'PY'
from ai_trading_brain import AITradingBrain, BrainContext
brain = AITradingBrain(data_dir='models/ai_trading_brain_test')
ctx = BrainContext(symbol='R_100', direction='CALL', signal_score=82, win_prob=0.64, confidence=0.62, payout=0.87, risk_allowed=True, market_regime='TREND')
d = brain.decide(ctx)
print(d)
brain.record_outcome(ctx, d, won=True, pnl=0.87, stake=1.0)
print(brain.status())
print(brain.evolve_once())
PY
```

## Patch order bắt buộc

1. Copy `ai_trading_brain/`
2. Smoke test module độc lập
3. Inject vào `DecisionEngine.__init__`
4. Inject vào `run_live_cycle` trước khi enqueue
5. Inject outcome record vào `_execute_trade`
6. Bật API control endpoints nếu dùng FastAPI
7. Chạy PAPER mode trước
8. Chạy LIVE với stake nhỏ
9. Sau đủ dữ liệu mới bật promotion

## Safety note

Patch này không đảm bảo lợi nhuận. Nó giúp hệ **ra quyết định có kiểm soát hơn**, nhớ kết quả tốt/xấu, và tiến hóa policy trong giới hạn rollback/kill-switch.
