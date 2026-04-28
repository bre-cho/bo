from api_server import create_app


def _route_methods(app):
    out = {}
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        out[path] = set(methods)
    return out


def test_frontend_wired_routes_exist_with_expected_methods():
    app = create_app()
    route_methods = _route_methods(app)

    expected = {
        ("GET", "/status"),
        ("GET", "/stats"),
        ("GET", "/balance"),
        ("GET", "/health/deriv"),
        ("GET", "/health/deriv/history"),
        ("GET", "/logs"),
        ("GET", "/audit/logs"),
        ("GET", "/db/evolution"),
        ("POST", "/db/trade_logs"),
        ("POST", "/db/model_versions"),
        ("POST", "/db/evolution_runs"),
        ("POST", "/engine/pause"),
        ("POST", "/engine/resume"),
        ("POST", "/strategy"),
        ("POST", "/strategy/reset"),
        ("POST", "/control/tp"),
        ("POST", "/control/sl"),
        ("POST", "/control/restart"),
        ("POST", "/control/wave"),
        ("POST", "/llm/ask"),
        ("POST", "/synthetic/train"),
        ("GET", "/synthetic/demo"),
        ("POST", "/evolution/run"),
        ("GET", "/evolution/status"),
        ("POST", "/evolution/promote"),
        ("POST", "/meta/breed"),
        ("GET", "/meta/report"),
        ("GET", "/meta/archetypes"),
        ("POST", "/causal/analyze"),
        ("GET", "/causal/report"),
        ("GET", "/causal/counterfactual"),
        ("POST", "/utility/optimize"),
        ("GET", "/utility/report"),
        ("GET", "/utility/pareto"),
        ("POST", "/gametheory/simulate"),
        ("GET", "/gametheory/report"),
    }

    missing = []
    for method, path in expected:
        if path not in route_methods or method not in route_methods[path]:
            missing.append(f"{method} {path}")

    assert not missing, f"Frontend/API route mismatch: {missing}"
