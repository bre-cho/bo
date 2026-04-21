from fastapi import FastAPI, Header, HTTPException, Depends
import config

app = FastAPI(title="Deriv Robot API")


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


@app.post("/engine/pause")
async def pause_engine(_: None = Depends(require_api_key)):
    return {"status": "paused"}


@app.post("/engine/resume")
async def resume_engine(_: None = Depends(require_api_key)):
    return {"status": "resumed"}


@app.post("/engine/stop")
async def stop_engine(_: None = Depends(require_api_key)):
    return {"status": "stopped"}
