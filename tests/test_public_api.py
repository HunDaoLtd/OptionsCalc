from __future__ import annotations

import pytest
import requests

from server import app as app_module
from server import binance
from server.risk_auth import RiskAuthManager


@pytest.fixture(autouse=True)
def isolated_snapshot_cache():
    binance.reset_snapshot_cache()
    yield
    binance.reset_snapshot_cache()


def test_public_api_error_boundary_hides_unexpected_runtime_error(monkeypatch, caplog):
    def failing_snapshot():
        raise RuntimeError("synthetic-private-runtime-diagnostic")

    monkeypatch.setattr(app_module, "fetch_btc_put_snapshot", failing_snapshot)
    app = app_module.create_app(
        risk_service=object(), risk_auth=RiskAuthManager.from_environment({})
    )

    response = app.test_client().get("/api/options/btc-put")

    assert response.status_code == 502
    assert response.get_json() == {"error": binance.PUBLIC_DATA_ERROR}
    assert "synthetic-private-runtime-diagnostic" not in caplog.text


@pytest.mark.parametrize("failure", ["proxy", "upstream"])
def test_uncached_public_api_failure_never_exposes_diagnostics(
    monkeypatch, caplog, failure
):
    secret = "synthetic-private-diagnostic"
    proxy_url = f"http://review-user:review-password@proxy.example:7890/{secret}"

    class FailedResponse:
        ok = False
        status_code = 502
        text = f"upstream echoed {proxy_url}"

    def fake_get(*args, **kwargs):
        if failure == "proxy":
            raise requests.exceptions.ProxyError(f"connection failed: {proxy_url}")
        return FailedResponse()

    monkeypatch.setattr(binance, "get_proxy_url", lambda: proxy_url)
    monkeypatch.setattr(binance.requests, "get", fake_get)
    app = app_module.create_app(
        risk_service=object(), risk_auth=RiskAuthManager.from_environment({})
    )

    response = app.test_client().get("/api/options/btc-put")

    assert response.status_code == 502
    assert response.get_json() == {"error": binance.PUBLIC_DATA_ERROR}
    for private_value in (secret, "review-user", "review-password", proxy_url):
        assert private_value not in response.get_data(as_text=True)
        assert private_value not in caplog.text
