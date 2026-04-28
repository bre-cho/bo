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

## Docker Runbook (api + worker + redis)

Muc tieu: team chay cung mot cach, cung bien moi truong, cung health checks.

### Services

- `api`: FastAPI server, startup command da bao gom `alembic upgrade head`.
- `worker`: engine runtime chay lien tuc bang `python robot.py`.
- `redis`: state store cho engine, memory, metrics, coordination.

### Env can thiet

Can tao file `.env` tai root (co the copy tu `.env.example`).

Gia tri toi thieu:

- `DERIV_API_TOKEN`
- `API_SECRET_KEY`
- `REDIS_HOST=redis`
- `REDIS_PORT=6379`

Luu y: khi chay bang docker compose, `REDIS_HOST` phai la `redis` (service name),
khong dung `localhost`.

### Start stack

```bash
docker compose up -d --build
```

### Kiem tra nhanh

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f worker
```

### Health checks can monitor

- Tong quan he thong:

```bash
curl -s http://localhost:8000/health
```

- Deriv deep health (chi tiet latency/timeout):

```bash
curl -s "http://localhost:8000/health/deriv?timeout_seconds=6"
```

Response co cac truong chinh:

- `token_present`
- `broker_reachable`
- `order_capable`
- `stage`
- `timeout_seconds`
- `latency_ms.connect|authorize|proposal|total`

### Smoke check mot lenh sau deploy

Script:

```bash
./scripts/smoke_check.sh http://localhost:8000
```

Yeu cau:

- da cai `curl`
- da cai `jq`

Script se kiem tra cac endpoint quan trong va fail-fast neu co route khong live.

### Connectivity map frontend -> backend

Script tu dong parse `frontend/lib/api.ts`, map API calls thanh danh sach route,
goi tung route live va bao cao PASS/FAIL.

```bash
./scripts/check_connectivity_map.sh http://localhost:8000
```

Co the truyen file api client khac (neu can):

```bash
./scripts/check_connectivity_map.sh http://localhost:8000 frontend/lib/api.ts
```

### Stop stack

```bash
docker compose down
```

### Reset state (neu can)

Can than: lenh nay xoa volume node_modules/.next cua frontend.

```bash
docker compose down -v
```
