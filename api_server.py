from fastapi import FastAPI, Header, HTTPException, Depends
import config

try:
    from ai_trading_brain.operator_api import router as ai_trading_brain_router
except Exception:
    ai_trading_brain_router = None

app = FastAPI(title="Deriv Robot API")
if ai_trading_brain_router is not None:
    app.include_router(ai_trading_brain_router)


# ============================================================
# Auth
# ============================================================

def require_api_key(x_api_key: str = Header(default="")):
    if x_api_key != config.API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ============================================================
# Routes
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/operator/manifest")
async def operator_manifest():
    from operator_manifest import OPERATOR_MANIFEST
    return OPERATOR_MANIFEST


@app.post("/operator/validate")
async def operator_validate(payload: dict):
    from operator_runtime import BOOperatorRuntime
    runtime = BOOperatorRuntime()
    return runtime.validate_execution_context(payload)


@app.post("/engine/pause")
async def pause_engine(_: None = Depends(require_api_key)):
    return {"status": "paused"}


@app.post("/engine/resume")
async def resume_engine(_: None = Depends(require_api_key)):
    return {"status": "resumed"}


@app.post("/engine/stop")
async def stop_engine(_: None = Depends(require_api_key)):
    return {"status": "stopped"}


@app.get("/engine/paper/summary")
async def paper_summary():
    """Trả về tóm tắt paper trading: số trades, avg score, phân bố chiều."""
    import json
    import redis as _redis
    r = _redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB)
    raw_entries = r.lrange("Deriv_Paper_Log", 0, -1)
    total = len(raw_entries)
    scores, dirs = [], {}
    for raw in raw_entries:
        try:
            e = json.loads(raw)
            scores.append(e.get("score", 0))
            d = e.get("direction", "?")
            dirs[d] = dirs.get(d, 0) + 1
        except Exception:
            pass
    promote_min = getattr(config, "PAPER_PROMOTE_MIN_TRADES", 50)
    promote_max = getattr(config, "PAPER_PROMOTE_MAX_TRADES", 100)
    return {
        "total_paper_trades": total,
        "avg_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "direction_distribution": dirs,
        "promote_min": promote_min,
        "promote_max": promote_max,
        "ready_for_live": total >= promote_min,
    }


@app.post("/engine/promote")
async def promote_to_live(_: None = Depends(require_api_key)):
    """Chuyển engine từ PAPER sang LIVE (yêu cầu đủ paper trades)."""
    import redis as _redis
    r = _redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB)
    paper_count = r.llen("Deriv_Paper_Log")
    promote_min = getattr(config, "PAPER_PROMOTE_MIN_TRADES", 50)
    if paper_count < promote_min:
        return {
            "status": "rejected",
            "reason": f"Cần tối thiểu {promote_min} paper trades, hiện có {paper_count}",
        }
    r.set("Deriv_EngineMode", "LIVE")
    return {"status": "promoted", "paper_trades_completed": paper_count, "mode": "LIVE"}

