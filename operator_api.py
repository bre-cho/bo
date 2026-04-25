from __future__ import annotations

from fastapi import APIRouter

from .brain import AITradingBrain

router = APIRouter(prefix="/v1/ai-trading-brain", tags=["ai-trading-brain"])

def _get_brain() -> AITradingBrain:
    """Lấy AITradingBrain có Redis client (lazy singleton).
    Dùng Redis kết nối từ config để sync state với DecisionEngine.
    """
    if not hasattr(_get_brain, "_instance"):
        try:
            import redis as _redis
            import config
            rc = _redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB)
            rc.ping()
            _get_brain._instance = AITradingBrain(redis_client=rc)
        except Exception:
            _get_brain._instance = AITradingBrain()
    return _get_brain._instance


@router.get("/status")
def status():
    return _get_brain().status()

@router.post("/evolve")
def evolve_once():
    return _get_brain().evolve_once()

@router.post("/promote")
def promote_candidate():
    return _get_brain().promote_candidate()

@router.post("/kill-switch")
def activate_kill_switch(reason: str = "manual"):
    _get_brain().governance.activate_kill_switch(reason)
    return {"ok": True, "active": True, "reason": reason}

@router.delete("/kill-switch")
def deactivate_kill_switch():
    _get_brain().governance.deactivate_kill_switch()
    return {"ok": True, "active": False}
