from __future__ import annotations

from server import app as app_module
from server.app import create_app
from tests.test_risk_service import make_service, valid_payload
from tests.conftest import mirror_proxy_cookie


def authenticated_client(service, risk_auth):
    app = create_app(service, risk_auth.manager)
    client = app.test_client()
    login = client.post(
        "/api/options/liquidation-risk/auth/login",
        json={"password": risk_auth.password},
        headers={"Origin": "http://localhost"},
    )
    assert login.status_code == 200
    mirror_proxy_cookie(client, login)
    return client


def test_accounts_and_evaluate_routes_apply_private_cache_policy_and_no_cors(
    risk_auth,
):
    client = authenticated_client(make_service(), risk_auth)

    accounts = client.get(
        "/api/options/liquidation-risk/accounts",
        headers={"Origin": "http://localhost"},
    )
    assert accounts.status_code == 200
    assert accounts.get_json()["defaultAccountId"] == "main"
    assert accounts.headers["Cache-Control"] == "no-store, private"
    assert "Access-Control-Allow-Origin" not in accounts.headers

    evaluation = client.post(
        "/api/options/liquidation-risk/evaluate",
        json=valid_payload(),
        headers={"Origin": "http://localhost"},
    )
    assert evaluation.status_code == 200
    assert evaluation.get_json()["accounts"][0]["status"] == "ok"
    assert evaluation.get_json()["warnings"][0]["code"] == (
        "open_orders_not_included"
    )
    assert evaluation.headers["Cache-Control"] == "no-store, private"
    assert "Access-Control-Allow-Origin" not in evaluation.headers

    cross_origin = client.post(
        "/api/options/liquidation-risk/evaluate",
        json=valid_payload(),
        headers={"Origin": "https://example.test"},
    )
    assert cross_origin.status_code == 403
    assert cross_origin.get_json() == {"error": {"code": "origin_not_allowed"}}


def test_evaluate_route_freezes_400_and_503_error_bodies(risk_auth):
    client = authenticated_client(make_service(), risk_auth)

    invalid = client.post(
        "/api/options/liquidation-risk/evaluate",
        data="{",
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert invalid.status_code == 400
    assert invalid.get_json() == {
        "error": {"code": "invalid_request", "issues": [{"code": "invalid_json"}]}
    }
    assert invalid.headers["Cache-Control"] == "no-store, private"
    assert "Access-Control-Allow-Origin" not in invalid.headers

    missing_service = make_service(
        account_registry=app_module.load_account_registry(
            {
                "BINANCE_OPTIONS_ACCOUNT_IDS": "main",
                "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID": "main",
                "BINANCE_OPTIONS_ACCOUNT_MAIN_API_KEY": "",
                "BINANCE_OPTIONS_ACCOUNT_MAIN_SECRET_KEY": "",
            }
        )
    )
    unavailable = authenticated_client(missing_service, risk_auth).post(
        "/api/options/liquidation-risk/evaluate",
        json=valid_payload(),
        headers={"Origin": "http://localhost"},
    )
    body = unavailable.get_json()
    assert unavailable.status_code == 503
    assert body["error"]["code"] == "service_unavailable"
    assert body["error"]["issues"][0]["code"] == "missing_credentials"
    assert unavailable.headers["Cache-Control"] == "no-store, private"
    assert "Access-Control-Allow-Origin" not in unavailable.headers

    invalid_registry = app_module.load_account_registry(
        {
            "BINANCE_OPTIONS_ACCOUNT_IDS": "main,main",
            "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID": "main",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_API_KEY": "key",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_SECRET_KEY": "secret",
        }
    )
    invalid_config = authenticated_client(
        make_service(account_registry=invalid_registry), risk_auth
    ).get("/api/options/liquidation-risk/accounts")
    assert invalid_config.status_code == 503
    assert invalid_config.get_json()["error"]["issues"][0]["code"] == (
        "configuration_error"
    )
    assert invalid_config.headers["Cache-Control"] == "no-store, private"
    assert "Access-Control-Allow-Origin" not in invalid_config.headers


def test_public_put_route_keeps_cors_and_existing_response_shape(monkeypatch, risk_auth):
    snapshot = {
        "underlying": "BTC",
        "indexPrice": 62000,
        "serverTime": 1,
        "updatedAt": 2,
        "contracts": [],
        "stale": False,
    }
    monkeypatch.setattr(app_module, "fetch_btc_put_snapshot", lambda: snapshot)
    client = create_app(make_service(), risk_auth.manager).test_client()

    response = client.get(
        "/api/options/btc-put",
        headers={"Origin": "https://example.test"},
    )

    assert response.status_code == 200
    assert response.get_json() == snapshot
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.test"
    assert response.headers.get("Cache-Control") != "no-store, private"
