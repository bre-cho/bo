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
from typing import List, Optional

import numpy as np
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
            raise HTTPException(status_code=400, detail=f"Strategy must be one of {sorted(allowed)}")
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

    # ── Synthetic Engine ──────────────────────────────────────────
    class SyntheticTrainRequest(BaseModel):
        n_per_regime   : int   = config.SYNTH_N_PER_REGIME
        blend_real_data: bool  = True

    @app.post("/synthetic/train")
    async def synthetic_train(req: SyntheticTrainRequest):
        """
        Trigger synthetic training on demand.
        Generates synthetic candles → trains WinClassifier + LSTM.
        """
        try:
            from synthetic_engine import run_full_synthetic_training
            # Optionally load real candle data for blending
            real_df = None
            if req.blend_real_data:
                try:
                    import deriv_data as _dd
                    real_df = _dd.fetch_candles(count=config.SIM_CANDLE_COUNT)
                except Exception:
                    pass
            metrics = run_full_synthetic_training(real_df=real_df, n_per_regime=req.n_per_regime)
            return {
                "status"    : "ok",
                "message"   : "Synthetic training complete",
                "metrics"   : metrics,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/synthetic/demo")
    async def synthetic_demo(n_per_regime: int = Query(default=30, ge=10, le=500)):
        """
        Preview synthetic dataset stats without training.
        Returns regime counts, win rate, feature vector shape.
        """
        try:
            from synthetic_engine import SyntheticScenarioLibrary
            lib  = SyntheticScenarioLibrary(seed=42)
            X, y = lib.build_dataset(n_per_regime=n_per_regime, balance=True)
            return {
                "n_samples"    : len(X),
                "win_rate_pct" : round(float(y.mean()) * 100, 1),
                "n_features"   : int(X.shape[1]) if len(X) > 0 else 0,
                "n_wins"       : int(y.sum()),
                "n_losses"     : int(len(y) - y.sum()),
                "feature_names": list(
                    __import__("feature_pipeline").FEATURE_NAMES
                ),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Evolution Engine ──────────────────────────────────────────
    class EvolutionRunRequest(BaseModel):
        generations : int  = config.EVOL_GENERATIONS
        pop_size    : int  = config.EVOL_POP_SIZE
        n_envs      : int  = config.EVOL_N_ENVIRONMENTS
        env_candles : int  = config.EVOL_ENV_CANDLES
        seed        : int  = 42

    @app.post("/evolution/run")
    async def evolution_run(req: EvolutionRunRequest):
        """
        Trigger a full evolution cycle (Self-Play + Genetic Algorithm).

        Runs synchronously (may take 30–120s depending on settings).
        Use small generations/pop_size for quick API tests.
        """
        try:
            from evolution_engine import run_evolution_cycle
            champion = run_evolution_cycle(
                generations = req.generations,
                pop_size    = req.pop_size,
                n_envs      = req.n_envs,
                env_candles = req.env_candles,
                seed        = req.seed,
                verbose     = False,
            )
            return {
                "status"    : "ok",
                "message"   : "Evolution complete",
                "champion"  : {
                    "genome_id"         : champion.genome_id,
                    "generation"        : champion.generation,
                    "fitness"           : round(champion.fitness, 6),
                    "win_rate_pct"      : round(champion.win_rate_pct, 2),
                    "profit_factor"     : round(champion.profit_factor, 4),
                    "n_trades"          : champion.n_trades,
                    "min_signal_score"  : round(champion.min_signal_score, 2),
                    "lookahead_candles" : champion.lookahead_candles,
                    "rsi_oversold"      : round(champion.rsi_oversold, 2),
                    "rsi_overbought"    : round(champion.rsi_overbought, 2),
                    "wave_weight"       : round(champion.wave_weight, 3),
                    "genes"             : {k: round(v, 4) for k, v in champion.genes().items()},
                },
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/evolution/status")
    async def evolution_status():
        """
        Return current champion genome + evolution history summary.
        """
        try:
            from evolution_engine import get_evolution_status
            return get_evolution_status()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/evolution/champion")
    async def evolution_champion():
        """Return current champion genome details."""
        try:
            from evolution_engine import load_champion
            champion = load_champion()
            if champion is None:
                return {"status": "no_champion", "champion": None}
            return {
                "status"  : "ok",
                "champion": champion.to_dict(),
                "genes"   : {k: round(v, 4) for k, v in champion.genes().items()},
                "summary" : champion.summary(),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/evolution/promote")
    async def evolution_promote():
        """Apply current champion genome params to live config."""
        try:
            from evolution_engine import apply_champion_to_config
            champion = apply_champion_to_config()
            if champion is None:
                return {"status": "no_champion", "applied": False}
            return {
                "status"           : "ok",
                "applied"          : True,
                "min_signal_score" : config.MIN_SIGNAL_SCORE,
                "rsi_oversold"     : config.RSI_OVERSOLD,
                "rsi_overbought"   : config.RSI_OVERBOUGHT,
                "lookahead_candles": config.SIM_LOOKAHEAD_CANDLES,
                "genome_id"        : champion.genome_id,
                "fitness"          : champion.fitness,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Meta-Learning (Strategy Genome Engine) ────────────────────

    @app.get("/meta/report")
    async def meta_report():
        """
        Return latest meta-learning report:
          gene importances, winner patterns, archetypes, insights.
        """
        try:
            from strategy_genetics import get_meta_report
            return get_meta_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class MetaBreedRequest(BaseModel):
        n_seeds: int = config.META_N_SEEDS

    @app.post("/meta/breed")
    async def meta_breed(req: MetaBreedRequest):
        """
        Run one meta-learning cycle and return bred seed genomes.
        Reads gene pool from Redis/file, analyzes, breeds, saves report.
        """
        try:
            from strategy_genetics import run_meta_cycle
            seeds, report = run_meta_cycle(n_seeds=req.n_seeds)
            return {
                "status"       : "ok",
                "n_seeds"      : len(seeds),
                "pool_size"    : report.pool_size,
                "n_archetypes" : report.n_archetypes,
                "top_genes"    : report.top_genes,
                "insights"     : report.insights,
                "seeds"        : [
                    {
                        "genome_id"        : s.genome_id,
                        "min_signal_score" : round(s.min_signal_score, 2),
                        "wave_weight"      : round(s.wave_weight, 3),
                        "lookahead_candles": s.lookahead_candles,
                        "genes"            : {k: round(v, 4) for k, v in s.genes().items()},
                    }
                    for s in seeds
                ],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/meta/archetypes")
    async def meta_archetypes():
        """Return current strategy archetypes from meta report."""
        try:
            from strategy_genetics import get_meta_report
            report = get_meta_report()
            if "status" in report:
                return {"status": "no_data", "archetypes": []}
            return {
                "status"    : "ok",
                "archetypes": report.get("archetypes", []),
                "n_winners" : report.get("n_winners_used", 0),
                "pool_size" : report.get("pool_size", 0),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/meta/gene_importance")
    async def meta_gene_importance():
        """Return gene importance scores from latest meta report."""
        try:
            from strategy_genetics import get_meta_report
            report = get_meta_report()
            if "status" in report:
                return {"status": "no_data", "importances": {}}
            return {
                "status"          : "ok",
                "gene_importances": report.get("gene_importances", {}),
                "top_genes"       : report.get("top_genes", []),
                "winner_patterns" : report.get("winner_patterns", {}),
                "insights"        : report.get("insights", []),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Causal Engine (World Model + Causal Strategic Intelligence) ─

    @app.get("/causal/report")
    async def causal_report():
        """
        Return latest causal analysis report:
          causal vs spurious genes, regime fitness maps,
          world model transition matrix, counterfactuals, insights.
        """
        try:
            from causal_engine import get_causal_report
            return get_causal_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class CausalAnalyzeRequest(BaseModel):
        fast_mode: bool = True

    @app.post("/causal/analyze")
    async def causal_analyze(req: CausalAnalyzeRequest):
        """
        Run full causal analysis on current gene pool.
        fast_mode=True: partial correlation proxy (fast).
        fast_mode=False: full do-calculus intervention (accurate, slower).
        """
        try:
            from causal_engine import run_causal_analysis, CausalEngine
            config.CAUSAL_FAST_MODE = req.fast_mode
            engine = CausalEngine()
            report = engine.run(verbose=False)
            return {
                "status"        : "ok",
                "pool_size"     : report.pool_size,
                "causal_genes"  : report.causal_genes,
                "spurious_genes": report.spurious_genes,
                "neutral_genes" : report.neutral_genes,
                "n_regimes"     : len(report.regime_fitness),
                "insights"      : report.insights,
                "top_effects"   : [
                    {
                        "gene"          : e["gene"],
                        "causal_ace"    : e["causal_ace"],
                        "spurious_score": e["spurious_score"],
                        "is_causal"     : e["is_causal"],
                        "is_spurious"   : e["is_spurious"],
                        "optimal_range" : e["optimal_range"],
                        "regime_stable" : e["regime_stable"],
                    }
                    for e in report.effects[:8]
                ],
                "counterfactuals": report.counterfactuals[:5],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/causal/counterfactual")
    async def causal_counterfactual(
        regime_from: str = "trend_up",
        regime_to  : str = "choppy",
    ):
        """
        Counterfactual query: if market switches from regime_from to regime_to,
        which genomes survive?
        """
        try:
            from causal_engine import get_causal_report
            report = get_causal_report()
            if "status" in report:
                return {"status": "no_data"}
            # Return precomputed counterfactuals filtered by regime pair
            cf_all = report.get("counterfactuals", [])
            cf_filtered = [
                cf for cf in cf_all
                if cf.get("regime_from") == regime_from
                   and cf.get("regime_to") == regime_to
            ] or cf_all  # fallback to all
            return {
                "status"          : "ok",
                "regime_from"     : regime_from,
                "regime_to"       : regime_to,
                "counterfactuals" : cf_filtered,
                "n_survived"      : sum(1 for cf in cf_filtered if cf.get("survived")),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/causal/world_model")
    async def causal_world_model():
        """Return regime transition probability matrix from world model."""
        try:
            from causal_engine import get_causal_report
            report = get_causal_report()
            if "status" in report:
                return {"status": "no_data"}
            return {
                "status"          : "ok",
                "world_model"     : report.get("world_model", {}),
                "regime_fitness"  : report.get("regime_fitness", [])[:8],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Utility Engine (Decision Theory + Utility Optimization) ────

    @app.get("/utility/report")
    async def utility_report():
        """
        Return latest utility optimization report:
          optimal genome, Kelly stake, Pareto front, utility breakdown, insights.
        """
        try:
            from utility_engine import get_utility_report
            return get_utility_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class UtilityOptimizeRequest(BaseModel):
        preset        : str   = config.UTILITY_DEFAULT_PRESET
        growth        : float = 0.35
        trust         : float = 0.30
        speed         : float = 0.20
        stability     : float = 0.15
        current_regime: str   = ""

    @app.post("/utility/optimize")
    async def utility_optimize(req: UtilityOptimizeRequest):
        """
        Run utility optimization with user-defined trade-off weights.
        preset overrides individual weights if not "custom".
        """
        try:
            from utility_engine import (
                run_utility_optimization, UtilityWeights, UtilityEngine
            )
            if req.preset and req.preset != "custom":
                weights = UtilityWeights.preset(req.preset)
            else:
                weights = UtilityWeights(
                    growth    = req.growth,
                    trust     = req.trust,
                    speed     = req.speed,
                    stability = req.stability,
                ).normalize()

            regime = req.current_regime or None
            engine = UtilityEngine(weights=weights)
            report = engine.run(current_regime=regime, verbose=False)
            return {
                "status"           : "ok",
                "optimal_genome_id": report.optimal_genome_id,
                "kelly_stake"      : report.kelly_stake,
                "pareto_front_size": report.pareto_front_size,
                "n_evaluated"      : report.n_evaluated,
                "utility_breakdown": report.utility_breakdown,
                "temporal_analysis": report.temporal_analysis,
                "pareto_front"     : report.pareto_front[:8],
                "insights"         : report.insights,
                "weights"          : report.weights,
                "top_scores"       : [
                    {
                        "genome_id"       : s["genome_id"],
                        "growth_utility"  : s["growth_utility"],
                        "trust_utility"   : s["trust_utility"],
                        "speed_utility"   : s["speed_utility"],
                        "stability_utility": s["stability_utility"],
                        "weighted_utility": s["weighted_utility"],
                        "kelly_fraction"  : s["kelly_fraction"],
                        "pareto_rank"     : s["pareto_rank"],
                        "is_pareto_optimal": s["is_pareto_optimal"],
                        "win_rate_pct"    : s["win_rate_pct"],
                    }
                    for s in report.scores[:10]
                ],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/utility/pareto")
    async def utility_pareto():
        """
        Run Pareto analysis across all weight presets.
        Returns how optimal genome changes under different preferences.
        """
        try:
            from utility_engine import UtilityEngine
            engine  = UtilityEngine()
            results = engine.pareto_analysis()
            return {"status": "ok", "pareto_by_preset": results}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/utility/kelly")
    async def utility_kelly(win_rate: float = 55.0):
        """
        Return Kelly fraction and growth curve for a given win rate.
        """
        try:
            from utility_engine import KellyOptimizer
            kelly = KellyOptimizer()
            kf, kf_frac, gr = kelly.compute(win_rate)
            return {
                "status"            : "ok",
                "win_rate_pct"      : win_rate,
                "kelly_full"        : kf,
                "kelly_fractional"  : kf_frac,
                "growth_rate"       : gr,
                "breakeven_winrate" : kelly.breakeven_winrate(),
                "curve"             : kelly.growth_curve(win_rate),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Game Theory Engine (Multi-Agent + Market Ecosystem) ────────

    @app.get("/gametheory/report")
    async def gametheory_report():
        """
        Return latest game theory report:
          Nash equilibrium, opponent model, EXP3 weights,
          ecosystem state, platform pressure.
        """
        try:
            from gametheory_engine import get_gametheory_report
            return get_gametheory_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class GameTheorySimRequest(BaseModel):
        current_regime  : str = ""
        n_rounds        : int = config.GAME_SIM_ROUNDS
        n_opponents     : int = config.GAME_N_OPPONENTS
        trade_outcomes  : List[int] = []   # 1=win, 0=loss

    @app.post("/gametheory/simulate")
    async def gametheory_simulate(req: GameTheorySimRequest):
        """
        Run full game theory analysis + ecosystem simulation.
        trade_outcomes: optional list of recent outcomes (1=win, 0=loss).
        """
        try:
            from gametheory_engine import GameTheoryEngine
            engine = GameTheoryEngine()
            config.GAME_SIM_ROUNDS  = req.n_rounds
            config.GAME_N_OPPONENTS = req.n_opponents
            outcomes = req.trade_outcomes or None
            report   = engine.run(
                trade_outcomes = outcomes,
                current_regime = req.current_regime or None,
                n_rounds       = req.n_rounds,
                verbose        = False,
            )
            return {
                "status"                 : "ok",
                "recommended_action"     : report.recommended_action,
                "nash_payoff"            : report.nash_payoff,
                "nash_solutions"         : report.nash_solutions[:2],
                "platform_pressure"      : report.platform_pressure,
                "crowding_index"         : report.crowding_index,
                "dominant_opponent"      : report.dominant_opponent,
                "opponent_concentration" : report.opponent_concentration,
                "opponent_beliefs"       : report.opponent_beliefs,
                "exp3_weights"           : report.exp3_weights,
                "pressure_analysis"      : report.pressure_analysis,
                "ecosystem_state"        : {
                    "n_rounds"          : report.ecosystem_state["n_rounds"],
                    "crowding_avg"      : report.ecosystem_state["crowding_avg"],
                    "pressure_final"    : report.ecosystem_state["pressure_final"],
                    "nash_distance"     : report.ecosystem_state["nash_distance"],
                    "our_state"         : report.ecosystem_state["our_state"],
                    "agent_states"      : report.ecosystem_state["agent_states"][:6],
                    "crowding_history"  : report.ecosystem_state["crowding_history"],
                    "pressure_history"  : report.ecosystem_state["pressure_history"],
                    "ecosystem_insights": report.ecosystem_state["ecosystem_insights"],
                },
                "insights"               : report.insights,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/gametheory/nash")
    async def gametheory_nash(
        win_prob : float = config.GAME_BASE_WIN_PROB,
        crowding : float = 0.0,
        pressure : float = 0.0,
    ):
        """
        Compute Nash equilibrium for given market conditions.
        win_prob: base win probability [0.4, 0.7].
        crowding: crowding discount factor [0, 1].
        pressure: platform pressure [0, 1].
        """
        try:
            from gametheory_engine import PayoffMatrix, NashSolver, ActionType as AT
            matrix = PayoffMatrix(
                base_win_prob     = float(np.clip(win_prob, 0.40, 0.75)),
                payout            = config.SIM_PAYOUT_RATIO,
                crowding_discount = float(np.clip(crowding, 0, 0.5)),
                platform_pressure = float(np.clip(pressure, 0, 0.8)),
            )
            solver    = NashSolver()
            nash_list = solver.solve(matrix)
            M         = matrix.matrix
            pne       = matrix.pure_nash_equilibria()
            return {
                "status"            : "ok",
                "win_prob"          : win_prob,
                "payoff_matrix"     : M.tolist(),
                "pure_nash"         : [(AT(r).name, AT(c).name) for r, c in pne],
                "mixed_nash"        : nash_list,
                "recommended_action": solver.recommended_action(nash_list),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/gametheory/opponents")
    async def gametheory_opponents():
        """
        Return opponent model beliefs from latest report.
        """
        try:
            from gametheory_engine import get_gametheory_report
            report = get_gametheory_report()
            if "status" in report:
                return {"status": "no_data"}
            return {
                "status"            : "ok",
                "opponent_beliefs"  : report.get("opponent_beliefs", {}),
                "dominant_opponent" : report.get("dominant_opponent", "?"),
                "exp3_weights"      : report.get("exp3_weights", {}),
                "platform_pressure" : report.get("platform_pressure", 0),
                "crowding_index"    : report.get("crowding_index", 0),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Sovereign Oversight Layer (SSOL) ──────────────────────────

    @app.get("/sovereign/report")
    async def sovereign_report():
        """
        Return latest Sovereign Oversight Layer report:
          network phase, cluster verdicts, resource budgets,
          guardrail alerts, strategic lessons, insights.
        """
        try:
            from sovereign_oversight import get_sovereign_report
            return get_sovereign_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class SovereignRunRequest(BaseModel):
        symbols    : List[str] = []
        verbose    : bool      = False

    @app.post("/sovereign/run")
    async def sovereign_run(req: SovereignRunRequest):
        """
        Trigger a full SSOL cycle on demand.

        symbols: list of cluster IDs (defaults to config.SCAN_SYMBOLS).
        Respects SSOL_SHADOW_MODE from config.
        """
        try:
            from sovereign_oversight import run_sovereign_cycle
            symbols = req.symbols or list(config.SCAN_SYMBOLS)
            report  = run_sovereign_cycle(active_symbols=symbols, verbose=req.verbose)
            return {
                "status"               : "ok",
                "network_phase"        : report.network_phase,
                "network_health_score" : report.network_health_score,
                "n_clusters_total"     : report.n_clusters_total,
                "n_clusters_active"    : report.n_clusters_active,
                "n_clusters_quarantined": report.n_clusters_quarantined,
                "n_clusters_dead"      : report.n_clusters_dead,
                "shadow_mode"          : report.shadow_mode,
                "cluster_verdicts"     : report.cluster_verdicts,
                "resource_budgets"     : report.resource_budgets,
                "guardrail_alerts"     : report.guardrail_alerts,
                "strategic_lessons"    : report.strategic_lessons,
                "insights"             : report.insights,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/sovereign/memory")
    async def sovereign_memory(n: int = Query(default=20, ge=1, le=100)):
        """
        Return recent strategic lessons from SSOL memory.

        Bài học được ghi khi cluster bị KILL/QUARANTINE/REVIVE.
        """
        try:
            from sovereign_oversight import StrategicMemory
            mem     = StrategicMemory()
            lessons = mem.get_recent(n=n)
            return {
                "status"  : "ok",
                "n"       : len(lessons),
                "lessons" : lessons,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class SovereignModeRequest(BaseModel):
        shadow_mode: bool

    @app.post("/sovereign/mode")
    async def sovereign_set_mode(req: SovereignModeRequest):
        """
        Toggle SSOL shadow mode.

        shadow_mode=True  → khuyến nghị chỉ (phase 2)
        shadow_mode=False → enforce verdicts (phase 3-4)
        """
        config.SSOL_SHADOW_MODE = req.shadow_mode
        return {
            "status"     : "ok",
            "shadow_mode": config.SSOL_SHADOW_MODE,
            "message"    : (
                "Shadow mode ON — chỉ log khuyến nghị"
                if req.shadow_mode
                else "Enforce mode ON — verdicts được áp dụng"
            ),
        }

    # ── Empire Control Layer (SSCL) ───────────────────────────────

    @app.get("/empire/report")
    async def empire_report():
        """
        Return latest Empire Control Layer report:
          dominance_score, attention_allocation, merge_proposals,
          portfolio_efficiency, objectives met/missed, insights.
        """
        try:
            from empire_control import get_empire_report
            return get_empire_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class EmpireRunRequest(BaseModel):
        symbols : List[str] = []
        verbose : bool      = False

    @app.post("/empire/run")
    async def empire_run(req: EmpireRunRequest):
        """
        Trigger a full SSCL cycle on demand.

        symbols: list of cluster IDs (defaults to config.SCAN_SYMBOLS).
        Returns full empire report with attention allocation and merge proposals.
        """
        try:
            from empire_control import run_empire_cycle
            symbols = req.symbols or list(config.SCAN_SYMBOLS)
            report  = run_empire_cycle(active_symbols=symbols, verbose=req.verbose)
            return {
                "status"              : "ok",
                "empire_phase"        : report.empire_phase,
                "dominance_score"     : report.dominance_score,
                "network_health_score": report.network_health_score,
                "attention_entropy"   : report.attention_entropy,
                "portfolio_efficiency": report.portfolio_efficiency,
                "attention_allocation": report.attention_allocation,
                "merge_proposals"     : report.merge_proposals,
                "objective"           : report.objective,
                "objectives_met"      : report.objectives_met,
                "objectives_missed"   : report.objectives_missed,
                "insights"            : report.insights,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/empire/history")
    async def empire_history(n: int = Query(default=50, ge=1, le=200)):
        """
        Return SSCL history: dominance_score + entropy trend over last N cycles.
        """
        try:
            from empire_control import get_empire_history
            history = get_empire_history(n=n)
            return {"status": "ok", "n": len(history), "history": history}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Autonomous Evolution Engine (AEE) ─────────────────────────

    @app.get("/evolution/aee/report")
    async def aee_report():
        """
        Return latest Autonomous Evolution Engine report:
          weaknesses, proposals, results, applied_mutations,
          evolution_safety, insights.
        """
        try:
            from autonomous_evolution import get_aee_report
            return get_aee_report()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class AEERunRequest(BaseModel):
        verbose     : bool = False
        cycle_count : int  = 0

    @app.post("/evolution/aee/run")
    async def aee_run(req: AEERunRequest):
        """
        Trigger a full AEE cycle on demand.

        Respects AEE_DRY_RUN from config.
        Set dry_run=false via /evolution/aee/mode first to enable apply.
        """
        try:
            from autonomous_evolution import run_autonomous_evolution
            report = run_autonomous_evolution(
                cycle_count=req.cycle_count,
                verbose=req.verbose,
            )
            return {
                "status"           : "ok",
                "n_weaknesses"     : report.n_weaknesses,
                "n_proposals"      : report.n_proposals,
                "n_passed"         : report.n_passed,
                "n_rejected"       : report.n_rejected,
                "n_blocked"        : report.n_blocked,
                "weaknesses"       : report.weaknesses[:5],
                "applied_mutations": report.applied_mutations,
                "evolution_safety" : report.evolution_safety,
                "insights"         : report.insights,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/evolution/aee/memory")
    async def aee_memory(n: int = Query(default=20, ge=1, le=100)):
        """
        Return recent evolution mutation history from AEE memory.

        Gồm mutations đã pass, reject, blocked — dùng để phân tích
        evolution trajectory và tránh lặp lại thất bại.
        """
        try:
            from autonomous_evolution import EvolutionMemory
            mem    = EvolutionMemory()
            history = mem.get_recent(n=n)
            return {"status": "ok", "n": len(history), "history": history}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    class AEEModeRequest(BaseModel):
        dry_run: bool

    @app.post("/evolution/aee/mode")
    async def aee_set_mode(req: AEEModeRequest):
        """
        Toggle AEE dry-run mode.

        dry_run=True  → detect + evaluate, không apply (safe default)
        dry_run=False → apply mutations đã pass gate (phase 3)
        """
        config.AEE_DRY_RUN = req.dry_run
        return {
            "status" : "ok",
            "dry_run": config.AEE_DRY_RUN,
            "message": (
                "Dry-run ON — mutations chỉ được đánh giá, không áp dụng"
                if req.dry_run
                else "Live mode ON — mutations đã pass gate sẽ được áp dụng ngay"
            ),
        }

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
