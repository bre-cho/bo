# BO MAIN — FULL AI TRADING BRAIN PATCH

Pack này bổ sung Decision + Memory + Evolution Engine vào `bo-main`.

## Apply

```bash
cp -r ai_trading_brain /path/to/bo-main/
cp -r tests /path/to/bo-main/
```

Đọc file:

```text
docs/FULL_AI_TRADING_BRAIN_IMPLEMENTATION.md
patches/decision_engine_integration_patch.md
```

## Verify

```bash
python -m py_compile ai_trading_brain/*.py
pytest tests/test_ai_trading_brain_smoke.py -q
```
