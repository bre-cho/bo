"""
tests/test_auth_and_health.py
Tests for:
  - /health deep-check structure
  - /deriv/check endpoint
  - API key auth enforcement on control routes
"""
import os
import pytest

from fastapi.testclient import TestClient

from api_server import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ──────────────────────────────────────────────────────────────
# /health
# ──────────────────────────────────────────────────────────────

def test_health_returns_status_field(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded")


def test_health_contains_checks(client):
    resp = client.get("/health")
    body = resp.json()
    assert "checks" in body
    checks = body["checks"]
    # All expected subsystem keys must be present
    for key in ("redis", "deriv_token", "model_files", "vector_store", "sqlite", "engine_mode"):
        assert key in checks, f"Missing check key: {key}"


def test_health_redis_check_has_status(client):
    checks = client.get("/health").json()["checks"]
    assert "status" in checks["redis"]


def test_health_deriv_token_check_has_status(client):
    checks = client.get("/health").json()["checks"]
    assert "status" in checks["deriv_token"]
    assert checks["deriv_token"]["status"] in ("configured", "missing")


# ──────────────────────────────────────────────────────────────
# /deriv/check + /health/deriv
# ──────────────────────────────────────────────────────────────

def test_deriv_check_returns_200(client):
    resp = client.get("/deriv/check")
    assert resp.status_code == 200


def test_health_deriv_returns_200(client):
    resp = client.get("/health/deriv")
    assert resp.status_code == 200


def test_health_deriv_history_returns_200(client):
    resp = client.get("/health/deriv/history")
    assert resp.status_code == 200


def test_health_safety_summary_returns_200(client):
    resp = client.get("/health/safety-summary")
    assert resp.status_code == 200


def test_deriv_check_has_expected_fields(client):
    body = client.get("/deriv/check").json()
    for field in (
        "configured",
        "token_present",
        "deriv_env",
        "token_source",
        "live_trading_enabled",
        "broker_reachable",
        "order_capable",
        "stage",
        "app_id",
        "ws_url",
        "symbol",
        "detail",
    ):
        assert field in body, f"Missing field: {field}"


def test_health_deriv_has_expected_fields(client):
    body = client.get("/health/deriv").json()
    for field in (
        "status",
        "configured",
        "token_present",
        "deriv_env",
        "token_source",
        "live_trading_enabled",
        "broker_reachable",
        "order_capable",
        "stage",
        "app_id",
        "ws_url",
        "symbol",
        "timeout_seconds",
        "latency_ms",
        "detail",
    ):
        assert field in body, f"Missing field: {field}"
    assert isinstance(body["latency_ms"], dict)
    for k in ("connect", "authorize", "proposal", "total"):
        assert k in body["latency_ms"], f"Missing latency key: {k}"


def test_health_deriv_history_has_expected_fields(client):
    body = client.get("/health/deriv/history").json()
    for field in ("status", "n", "records"):
        assert field in body, f"Missing field: {field}"
    assert body["status"] in ("ok", "degraded")
    assert isinstance(body["records"], list)


def test_health_safety_summary_has_expected_fields(client):
    body = client.get("/health/safety-summary").json()
    for field in (
        "status",
        "engine_mode",
        "deriv_env",
        "token_source",
        "token_present",
        "live_trading_enabled",
        "live_switch_state",
        "armed",
        "can_execute_live",
    ):
        assert field in body, f"Missing field: {field}"
    assert body["status"] == "ok"
    assert body["live_switch_state"] in ("armed", "disarmed")


def test_health_database_backend_is_sqlite(client):
    body = client.get("/health").json()
    assert "database" in body["checks"]
    assert body["checks"]["database"]["backend"] == "sqlite"


def test_deriv_check_configured_false_when_no_token(client, monkeypatch):
    monkeypatch.setenv("DERIV_API_TOKEN", "")
    import config as cfg
    original = cfg.DERIV_API_TOKEN
    cfg.DERIV_API_TOKEN = ""
    try:
        body = client.get("/deriv/check").json()
        # Khi token rong, tat ca live checks phai false
        assert body["configured"] is False
        assert body["token_present"] is False
        assert body["broker_reachable"] is False
        assert body["order_capable"] is False
        assert body["token_hint"] is None
        assert body["stage"] == "token"
    finally:
        cfg.DERIV_API_TOKEN = original


# ──────────────────────────────────────────────────────────────
# API key auth on control endpoints
# ──────────────────────────────────────────────────────────────

_CONTROL_POSTS = [
    ("/control/tp",      {"amount_usd": 10}),
    ("/control/sl",      {"amount_usd": 5}),
    ("/control/wave",    {"mode": "both"}),
    ("/control/restart", {}),
    ("/engine/pause",    {}),
    ("/engine/resume",   {}),
    ("/engine/stop",     {}),
    ("/strategy",        {"name": "fixed_fractional", "base_stake": 1.0}),
    ("/strategy/reset",  {}),
    ("/db/trade_logs",   {"symbol": "R_100", "direction": "CALL", "stake_usd": 1.0}),
    ("/db/model_versions", {
        "model_name": "win_classifier",
        "version_tag": "v_test",
        "is_active": False,
    }),
    ("/db/evolution_runs", {
        "genome_id": "g_test",
        "generation": 1,
        "fitness": 0.5,
    }),
]


def _api_key_unconfigured():
    """Return True when API_SECRET_KEY is blank or the changeme placeholder."""
    import config as cfg
    key = os.environ.get("API_SECRET_KEY", "") or getattr(cfg, "API_SECRET_KEY", "")
    return not key or key in ("", "changeme_in_env")


@pytest.mark.parametrize("path,body", _CONTROL_POSTS)
def test_control_routes_reject_no_api_key(client, path, body):
    """Control endpoints must not be reachable without an X-API-Key header."""
    resp = client.post(path, json=body)
    # When key is unconfigured → 503 (configuration error)
    # When key is configured but missing from request → 401
    if _api_key_unconfigured():
        assert resp.status_code == 503, (
            f"{path}: expected 503 (key not configured) got {resp.status_code}"
        )
    else:
        assert resp.status_code == 401, (
            f"{path}: expected 401 (missing key) got {resp.status_code}"
        )


@pytest.mark.parametrize("path,body", _CONTROL_POSTS)
def test_control_routes_reject_wrong_api_key(client, path, body, monkeypatch):
    """Control endpoints must reject an incorrect X-API-Key."""
    import config as cfg
    monkeypatch.setattr(cfg, "API_SECRET_KEY", "correct_key_for_test")
    monkeypatch.setenv("API_SECRET_KEY", "correct_key_for_test")
    resp = client.post(path, json=body, headers={"X-API-Key": "wrong_key"})
    assert resp.status_code == 401, (
        f"{path}: expected 401 (wrong key) got {resp.status_code}"
    )
