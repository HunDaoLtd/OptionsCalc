from __future__ import annotations

import pytest
import requests

from server import binance


def test_combines_exchange_info_mark_prices_and_index(monkeypatch):
    now = 1780891200000
    expiry = now + 10 * 24 * 60 * 60 * 1000

    def fake_request_json(path: str):
        if "/exchangeInfo" in path:
            return {
                "serverTime": now,
                "optionSymbols": [
                    {
                        "symbol": "BTC-260618-62000-P",
                        "side": "PUT",
                        "expiryDate": expiry,
                        "strikePrice": "62000",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "BTC-260618-62000-C",
                        "side": "CALL",
                        "expiryDate": expiry,
                        "strikePrice": "62000",
                        "status": "TRADING",
                    },
                ],
            }
        if "/mark" in path:
            return [{"symbol": "BTC-260618-62000-P", "markPrice": "1000", "markIV": "0.625"}]
        return {"indexPrice": "62500"}

    monkeypatch.setattr(binance, "request_json", fake_request_json)
    binance.reset_snapshot_cache()

    snapshot = binance.fetch_btc_put_snapshot(now)

    assert snapshot["indexPrice"] == 62500
    assert len(snapshot["contracts"]) == 1
    assert snapshot["contracts"][0]["symbol"] == "BTC-260618-62000-P"
    assert snapshot["contracts"][0]["strike"] == 62000
    assert snapshot["contracts"][0]["markPrice"] == 1000
    assert snapshot["contracts"][0]["markIV"] == 0.625
    assert snapshot["contracts"][0]["daysToExpiry"] == 10


def test_filters_non_displayable_contracts(monkeypatch):
    now = 1780891200000
    future_expiry = now + 7 * 24 * 60 * 60 * 1000
    past_expiry = now - 24 * 60 * 60 * 1000

    def fake_request_json(path: str):
        if "/exchangeInfo" in path:
            return {
                "serverTime": now,
                "optionSymbols": [
                    {
                        "symbol": "BTC-260615-61000-P",
                        "side": "PUT",
                        "expiryDate": future_expiry,
                        "strikePrice": "61000",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "BTC-260615-61000-C",
                        "side": "CALL",
                        "expiryDate": future_expiry,
                        "strikePrice": "61000",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "BTC-260607-60000-P",
                        "side": "PUT",
                        "expiryDate": past_expiry,
                        "strikePrice": "60000",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "ETH-260615-3000-P",
                        "side": "PUT",
                        "expiryDate": future_expiry,
                        "strikePrice": "3000",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "BTC-260615-59000-P",
                        "side": "PUT",
                        "expiryDate": future_expiry,
                        "strikePrice": "59000",
                        "status": "SETTLING",
                    },
                ],
            }
        if "/mark" in path:
            return [{"symbol": "BTC-260615-61000-P", "markPrice": "500", "markIV": "0.6"}]
        return {"indexPrice": "62000"}

    monkeypatch.setattr(binance, "request_json", fake_request_json)
    binance.reset_snapshot_cache()

    snapshot = binance.fetch_btc_put_snapshot(now)

    assert [contract["symbol"] for contract in snapshot["contracts"]] == ["BTC-260615-61000-P"]


def test_returns_stale_snapshot_after_later_failure(monkeypatch, caplog):
    now = 1780891200000
    expiry = now + 3 * 24 * 60 * 60 * 1000
    exchange_info = {
        "serverTime": now,
        "optionSymbols": [
            {
                "symbol": "BTC-260611-60000-P",
                "side": "PUT",
                "expiryDate": expiry,
                "strikePrice": "60000",
                "status": "TRADING",
            }
        ],
    }

    def fake_request_json(path: str):
        if "/exchangeInfo" in path:
            return exchange_info
        if "/mark" in path:
            return [
                {
                    "symbol": "BTC-260611-60000-P",
                    "markPrice": "250",
                    "markIV": "0.55",
                }
            ]
        return {"indexPrice": "61500"}

    monkeypatch.setattr(binance, "request_json", fake_request_json)
    binance.reset_snapshot_cache()

    fresh = binance.fetch_btc_put_snapshot(now)

    def failing_request_json(_path: str):
        raise RuntimeError(
            "synthetic-private-network-diagnostic "
            "http://review-user:review-password@proxy.example:7890/private-path"
        )

    monkeypatch.setattr(binance, "request_json", failing_request_json)
    stale = binance.fetch_btc_put_snapshot(now + 60_000)

    assert fresh["stale"] is False
    assert stale["stale"] is True
    assert stale["sourceError"] == binance.PUBLIC_DATA_ERROR
    for private_value in (
        "synthetic-private-network-diagnostic",
        "review-user",
        "review-password",
        "private-path",
    ):
        assert private_value not in str(stale)
        assert private_value not in caplog.text
    assert stale["contracts"][0]["symbol"] == "BTC-260611-60000-P"


def test_reuses_fresh_snapshot_cache(monkeypatch):
    now = 1780891200000
    expiry = now + 2 * 24 * 60 * 60 * 1000
    calls: list[str] = []

    def fake_request_json(path: str):
        calls.append(path)
        if "/exchangeInfo" in path:
            return {
                "serverTime": now,
                "optionSymbols": [
                    {
                        "symbol": "BTC-260610-60000-P",
                        "side": "PUT",
                        "expiryDate": expiry,
                        "strikePrice": "60000",
                        "status": "TRADING",
                    }
                ],
            }
        if "/mark" in path:
            return [{"symbol": "BTC-260610-60000-P", "markPrice": "300", "markIV": "0.5"}]
        return {"indexPrice": "61000"}

    monkeypatch.setattr(binance, "request_json", fake_request_json)
    binance.reset_snapshot_cache()

    first = binance.fetch_btc_put_snapshot(now)
    second = binance.fetch_btc_put_snapshot(now + 5_000)

    assert first["contracts"][0]["symbol"] == "BTC-260610-60000-P"
    assert second["contracts"][0]["symbol"] == "BTC-260610-60000-P"
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("proxy_url", "safe_address"),
    [
        (
            "http://review-user:review-password@proxy.example:7890/"
            "private-path?query-secret#fragment-secret",
            "proxy.example:7890",
        ),
        (
            "http://review-user:review-password@[2001:db8::1]:7890/"
            "private-path?query-secret#fragment-secret",
            "[2001:db8::1]:7890",
        ),
        (
            "http://review-user:review-password@[broken/private-path?query-secret",
            "unavailable",
        ),
        (
            "http://review-user:review-password@proxy.example:invalid/private-path",
            "unavailable",
        ),
    ],
)
def test_proxy_fallback_logs_only_safe_address_and_error_class(
    monkeypatch, caplog, proxy_url, safe_address
):
    calls = []

    def fake_request(path, proxy):
        calls.append((path, proxy))
        if proxy is not None:
            raise requests.exceptions.ProxyError(
                f"synthetic-private-exception: {proxy_url}"
            )
        return {"ok": True}

    monkeypatch.setattr(binance, "get_proxy_url", lambda: proxy_url)
    monkeypatch.setattr(binance, "request_json_with_proxy", fake_request)

    assert binance.request_json("/eapi/v1/exchangeInfo") == {"ok": True}
    assert calls == [
        ("/eapi/v1/exchangeInfo", proxy_url),
        ("/eapi/v1/exchangeInfo", None),
    ]
    assert f"proxy={safe_address}" in caplog.text
    assert "error=ProxyError" in caplog.text
    for private_value in (
        "review-user",
        "review-password",
        "private-path",
        "query-secret",
        "fragment-secret",
        "synthetic-private-exception",
        "/eapi/v1/exchangeInfo",
    ):
        assert private_value not in caplog.text


def test_upstream_http_error_never_logs_response_body(monkeypatch, caplog):
    class FailedResponse:
        ok = False
        status_code = 502

        @property
        def text(self):
            pytest.fail("Upstream response body must not be read for logging")

    monkeypatch.setattr(binance.requests, "get", lambda *args, **kwargs: FailedResponse())

    with pytest.raises(RuntimeError, match=binance.PUBLIC_DATA_ERROR):
        binance.request_json_with_proxy("/eapi/v1/exchangeInfo", None)

    assert "status=502" in caplog.text
    assert "/eapi/v1/exchangeInfo" not in caplog.text
