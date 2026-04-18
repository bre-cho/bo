"""
api_server.py
=============
FastAPI REST API cho dashboard và điều khiển robot.

Endpoints:
  GET  /status          — Trạng thái hệ thống realtime
  GET  /stats           — Thống kê giao dịch
  GET  /balance         — Số dư tài khoản
  POST /control/tp      — Đặt Daily Take-Profit
  POST /control/sl      — Đặt Daily Stop-Loss
  POST /control/wave    — Đặt Wave Direction Filter
  POST /control/restart — Restart sau khi bị dừng bởi TP/SL
  POST /strategy        — Đổi chiến lược vốn
  GET  /candle_library  — Trạng thái thư viện nến
  POST /engine/pause    — Tạm dừng engine (chuyển PAPER)
  POST /engine/resume   — Tiếp tục (chuyển LIVE)
  GET  /llm/ask         — Hỏi LLM analyst
  GET  /logs            — Trade log (paginated)
  GET  /memory/report   — Memory Brain report
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

import redis

import config


# ──────────────────────────────────────────────────────────────────
# Lazy FastAPI import
# ──────────────────────────────────────────────────────────────────

try:
    from fastapi import FastAPI, HTTPException, Depends, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False
    print("[API] FastAPI not installed — API server disabled")


# ──────────────────────────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────────────────────────

if _HAS_FASTAPI:
    class DailyLimitRequest(BaseModel):
        amount_usd: float

    class WaveFilterRequest(BaseModel):
        mode: str  # "both" | "up_only" | "down_only"

    class StrategyRequest(BaseModel):
        name:       str
        base_stake: float = 1.0

    class LLMRequest(BaseModel):
        question: str

    class ControlRequest(BaseModel):
        pass


# ──────────────────────────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────────────────────────

def create_app():
    if not _HAS_FASTAPI:
        raise RuntimeError("FastAPI not installed. Run: pip install fastapi uvicorn")

    app = FastAPI(
        title       = "BO Trading Robot API",
        description = "Control panel for the autonomous trading system",
        version     = "2.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins     = config.API_CORS_ORIGINS,
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── Shared state via Redis ────────────────────────────────────
    _r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB)

    def get_redis():
        return _r

    # ── Lazy component init ───────────────────────────────────────
    _components: dict = {}

    def get_control():
        if "control" not in _components:
            from control_system import ControlSystem
            _components["control"] = ControlSystem()
        return _components["control"]

    def get_cap_strat():
        if "cap_strat" not in _components:
            from capital_strategy import CapitalStrategyManager
            _components["cap_strat"] = CapitalStrategyManager()
        return _components["cap_strat"]

    def get_logger():
        if "logger" not in _components:
            from logger import TradeLogger
            _components["logger"] = TradeLogger()
        return _components["logger"]

    def get_vector_store():
        if "vs" not in _components:
            from vector_store import VectorStore
            _components["vs"] = VectorStore()
        return _components["vs"]

    def get_llm_agent():
        if "llm" not in _components:
            from llm_agent import LLMAgent
            _components["llm"] = LLMAgent(
                logger       = get_logger(),
                vector_store = get_vector_store(),
                control      = get_control(),
            )
        return _components["llm"]

    # ── Serve static frontend ─────────────────────────────────────
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    if os.path.isdir(frontend_dir):
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

        @app.get("/", include_in_schema=False)
        async def root():
            index_path = os.path.join(frontend_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            return {"status": "BO Trading Robot API", "docs": "/docs"}
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"status": "BO Trading Robot API", "docs": "/docs"}

    # ── Status ────────────────────────────────────────────────────
    @app.get("/status")
    async def get_status(r: redis.Redis = Depends(get_redis)):
        mode   = r.get("Deriv_EngineMode")
        mode   = mode.decode() if mode else "UNKNOWN"
        syms   = r.get("Deriv_ActiveSymbols")
        syms   = json.loads(syms) if syms else list(config.SCAN_SYMBOLS)

        ctrl   = get_control()
        capst  = get_cap_strat()

        return {
            "timestamp"      : datetime.now().isoformat(),
            "engine_mode"    : mode,
            "active_symbols" : syms,
            "control"        : ctrl.status(),
            "capital_strategy": capst.status(),
        }

    # ── Stats ─────────────────────────────────────────────────────
    @app.get("/stats")
    async def get_stats():
        return get_logger().get_stats()

    # ── Trade logs ────────────────────────────────────────────────
    @app.get("/logs")
    async def get_logs(
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        r: redis.Redis = Depends(get_redis),
    ):
        raw_list = r.lrange(config.REDIS_LOG_KEY, 0, -1)
        records  = [json.loads(x) for x in raw_list]
        start    = (page - 1) * size
        end      = start + size
        return {
            "total"  : len(records),
            "page"   : page,
            "size"   : size,
            "records": records[start:end],
        }

    # ── Balance (read-only) ───────────────────────────────────────
    @app.get("/balance")
    async def get_balance_endpoint():
        try:
            from deriv_trade import get_balance
            bal = get_balance()
            return {"balance": bal, "currency": config.TRADE_CURRENCY}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    # ── Control: Daily TP ─────────────────────────────────────────
    @app.post("/control/tp")
    async def set_daily_tp(req: DailyLimitRequest):
        ctrl = get_control()
        ctrl.set_daily_tp(req.amount_usd)
        return {"status": "ok", "daily_take_profit_usd": req.amount_usd}

    # ── Control: Daily SL ─────────────────────────────────────────
    @app.post("/control/sl")
    async def set_daily_sl(req: DailyLimitRequest):
        ctrl = get_control()
        ctrl.set_daily_sl(req.amount_usd)
        return {"status": "ok", "daily_stop_loss_usd": req.amount_usd}

    # ── Control: Wave filter ──────────────────────────────────────
    @app.post("/control/wave")
    async def set_wave_filter(req: WaveFilterRequest):
        ctrl = get_control()
        ctrl.set_wave_filter(req.mode)
        return {"status": "ok", "wave_direction_filter": req.mode}

    # ── Control: Restart after TP/SL stop ────────────────────────
    @app.post("/control/restart")
    async def restart_after_stop():
        ctrl = get_control()
        ctrl.reset_daily_stop()
        return {"status": "ok", "message": "Đã khởi động lại — tiếp tục giao dịch"}

    # ── Capital Strategy ──────────────────────────────────────────
    @app.post("/strategy")
    async def set_strategy(req: StrategyRequest):
        allowed = {"fixed_fractional","martingale","anti_martingale","victor2","victor3","victor4"}
        if req.name not in allowed:
            raise HTTPException(status_code=400, detail=f"Strategy must be one of {allowed}")
        capst = get_cap_strat()
        capst.set_strategy(req.name, req.base_stake)
        return {"status": "ok", "strategy": req.name, "base_stake": req.base_stake}

    @app.post("/strategy/reset")
    async def reset_strategy():
        get_cap_strat().reset()
        return {"status": "ok", "message": "Strategy state reset"}

    # ── Engine control (via Redis signals) ───────────────────────
    @app.post("/engine/pause")
    async def pause_engine(r: redis.Redis = Depends(get_redis)):
        r.set("Deriv_EngineMode", "PAPER")
        return {"status": "ok", "engine_mode": "PAPER"}

    @app.post("/engine/resume")
    async def resume_engine(r: redis.Redis = Depends(get_redis)):
        r.set("Deriv_EngineMode", "LIVE")
        return {"status": "ok", "engine_mode": "LIVE"}

    # ── Candle library status ─────────────────────────────────────
    @app.get("/candle_library")
    async def candle_library_status():
        from candle_library import CandleLibraryManager
        mgr   = CandleLibraryManager()
        mgr.load_all()
        return {"libraries": mgr.stats_all()}

    # ── LLM Ask ───────────────────────────────────────────────────
    @app.post("/llm/ask")
    async def llm_ask(req: LLMRequest):
        agent  = get_llm_agent()
        answer = agent.ask(req.question)
        return {"question": req.question, "answer": answer}

    # ── Memory report ─────────────────────────────────────────────
    @app.get("/memory/report")
    async def memory_report(r: redis.Redis = Depends(get_redis)):
        from memory import MemoryBrain
        brain = MemoryBrain()
        # Return memory stats from Redis
        raw_stats = r.get(config.REDIS_MEMORY_STATS_KEY)
        stats = json.loads(raw_stats) if raw_stats else {}
        raw_rules = r.get(config.REDIS_MEMORY_RULES_KEY)
        rules = json.loads(raw_rules) if raw_rules else []
        return {
            "stats"      : stats,
            "hard_rules" : rules[:50],
            "total_rules": len(rules),
        }

    # ── Model registry ────────────────────────────────────────────
    @app.get("/models/registry")
    async def models_registry():
        from model_registry import ModelRegistry
        reg = ModelRegistry()
        return {"report": reg.report(), "versions": reg._state.versions[-20:]}

    return app


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not _HAS_FASTAPI:
        print("Install FastAPI: pip install fastapi uvicorn")
    else:
        import uvicorn
        app = create_app()
        uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, reload=False)
