from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from server.risk_accounts import load_account_registry
from server.risk_binance import (
    BinancePrivateClient,
    BinancePrivateError,
    PrivateAccountCache,
    RiskPublicCache,
    build_signed_query,
    normalize_risk_public_snapshot,
)
from server.risk_types import OptionSide, RiskIssueCode


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "liquidation_risk"
    / "classic-options-snapshot.json"
)


def account_config():
    registry = load_account_registry(
        {
            "BINANCE_OPTIONS_ACCOUNT_IDS": "main",
            "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID": "main",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_API_KEY": "api-key",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_SECRET_KEY": "secret-key",
        }
    )
    return registry.accounts[0]


class FakeResponse:
    def __init__(self, status_code: int, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_build_signed_query_uses_canonical_hmac_sha256():
    parameters = (("timestamp", 123), ("recvWindow", 5000))
    result = build_signed_query(parameters, "secret-key")
    query = "timestamp=123&recvWindow=5000"
    expected = hmac.new(
        b"secret-key", query.encode(), hashlib.sha256
    ).hexdigest()
    assert result == f"{query}&signature={expected}"


def test_private_client_signs_allowlisted_get_and_reuses_time_offset(monkeypatch):
    monkeypatch.setattr("server.risk_binance.get_proxy_url", lambda: None)
    session = FakeSession(
        [
            FakeResponse(200, {"serverTime": 1_000_500}),
            FakeResponse(200, {"asset": []}),
            FakeResponse(200, []),
        ]
    )
    client = BinancePrivateClient(session=session, now_seconds=lambda: 1_000.0)
    config = account_config()

    assert client.get(config, "/eapi/v1/marginAccount") == {"asset": []}
    assert client.get(config, "/eapi/v1/position") == []

    assert len(session.calls) == 3
    signed_call = session.calls[1]
    assert signed_call["headers"] == {"X-MBX-APIKEY": "api-key"}
    parsed = parse_qs(urlsplit(signed_call["url"]).query)
    assert parsed["timestamp"] == ["1000500"]
    assert parsed["recvWindow"] == ["5000"]
    assert len(parsed["signature"][0]) == 64
    assert "secret-key" not in json.dumps(session.calls)


def test_private_client_refreshes_time_once_for_timestamp_error(monkeypatch):
    monkeypatch.setattr("server.risk_binance.get_proxy_url", lambda: None)
    session = FakeSession(
        [
            FakeResponse(200, {"serverTime": 1_000_000}),
            FakeResponse(400, {"code": -1021}),
            FakeResponse(200, {"serverTime": 1_000_100}),
            FakeResponse(200, {"asset": []}),
        ]
    )
    client = BinancePrivateClient(session=session, now_seconds=lambda: 1_000.0)

    result = client.get(account_config(), "/eapi/v1/marginAccount")

    assert result == {"asset": []}
    assert len(session.calls) == 4


@pytest.mark.parametrize("status_code", [418, 429])
def test_private_client_rate_limit_sets_non_blocking_global_cooldown(
    monkeypatch, status_code
):
    monkeypatch.setattr("server.risk_binance.get_proxy_url", lambda: None)
    clock = [1_000.0]
    session = FakeSession(
        [
            FakeResponse(200, {"serverTime": 1_000_000}),
            FakeResponse(status_code, {"code": -1003}, {"Retry-After": "5"}),
        ]
    )
    client = BinancePrivateClient(session=session, now_seconds=lambda: clock[0])

    with pytest.raises(BinancePrivateError) as first:
        client.get(account_config(), "/eapi/v1/marginAccount")
    assert first.value.retry_after_seconds == 5

    with pytest.raises(BinancePrivateError) as cooled:
        client.get(account_config(), "/eapi/v1/position")
    assert cooled.value.retry_after_seconds == pytest.approx(5)
    assert len(session.calls) == 2


def test_private_client_normalizes_timeout_and_rejects_non_allowlisted_path(monkeypatch):
    monkeypatch.setattr("server.risk_binance.get_proxy_url", lambda: None)
    session = FakeSession([requests.Timeout("timeout")])
    client = BinancePrivateClient(session=session, now_seconds=lambda: 1_000.0)

    with pytest.raises(BinancePrivateError) as timeout:
        client.get(account_config(), "/eapi/v1/marginAccount")
    assert timeout.value.issue_code is RiskIssueCode.UPSTREAM_UNAVAILABLE
    with pytest.raises(ValueError, match="allowlisted"):
        client.get(account_config(), "/eapi/v1/not-allowlisted")


def test_private_client_logs_never_include_credentials_signature_or_payload(
    monkeypatch, caplog
):
    api_key = "stage-f-api-key"
    secret_key = "stage-f-secret-key"
    registry = load_account_registry(
        {
            "BINANCE_OPTIONS_ACCOUNT_IDS": "main",
            "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID": "main",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_API_KEY": api_key,
            "BINANCE_OPTIONS_ACCOUNT_MAIN_SECRET_KEY": secret_key,
        }
    )
    session = FakeSession(
        [
            FakeResponse(200, {"serverTime": 1_000_000}),
            FakeResponse(
                400,
                {
                    "code": -2015,
                    "msg": f"rejected {api_key} {secret_key} account-balance=12345",
                },
            ),
        ]
    )
    monkeypatch.setattr("server.risk_binance.get_proxy_url", lambda: None)
    client = BinancePrivateClient(session=session, now_seconds=lambda: 1_000.0)

    with caplog.at_level(logging.WARNING, logger="server.risk_binance"):
        with pytest.raises(BinancePrivateError):
            client.get(registry.accounts[0], "/eapi/v1/marginAccount")

    logs = caplog.text
    signed_url = session.calls[1]["url"]
    signature = parse_qs(urlsplit(signed_url).query)["signature"][0]
    assert api_key not in logs
    assert secret_key not in logs
    assert signature not in logs
    assert "account-balance=12345" not in logs
    assert "Authorization" not in logs


class FakePrivateClient:
    def __init__(self):
        self.calls = 0
        self.failure: BinancePrivateError | None = None

    def get(self, _account, path):
        self.calls += 1
        if self.failure:
            raise self.failure
        return {"asset": []} if path.endswith("marginAccount") else []


def test_private_cache_handles_ttl_force_refresh_stale_and_cutoff():
    client = FakePrivateClient()
    cache = PrivateAccountCache(client, ttl_seconds=10, stale_max_seconds=60)
    config = account_config()

    first = cache.fetch(config, now_ms=1_000)
    assert cache.fetch(config, now_ms=5_000) is first
    assert client.calls == 2
    assert "asset" not in repr(first)
    cache.fetch(config, force_refresh=True, now_ms=6_000)
    assert client.calls == 4

    client.failure = BinancePrivateError(
        RiskIssueCode.UPSTREAM_UNAVAILABLE, "/eapi/v1/marginAccount"
    )
    stale = cache.fetch(config, now_ms=20_000)
    assert stale.stale is True
    assert stale.fetched_at_ms == 6_000
    with pytest.raises(BinancePrivateError) as expired:
        cache.fetch(config, now_ms=70_001)
    assert expired.value.issue_code is RiskIssueCode.STALE_DATA_EXPIRED


def test_public_risk_normalization_contains_all_fixture_calls_and_puts():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    responses = fixture["responses"]
    snapshot = normalize_risk_public_snapshot(
        responses["exchangeInfo"],
        responses["mark"],
        responses["index"],
        fetched_at_ms=fixture["capturedAtMs"],
    )

    assert snapshot.market.index_price == 62885.16
    assert len(snapshot.contracts) == 6
    assert {contract.option_side for contract in snapshot.contracts} == {
        OptionSide.CALL,
        OptionSide.PUT,
    }
    assert all(contract.mark_price is not None for contract in snapshot.contracts)
    assert all(contract.mark_iv is not None for contract in snapshot.contracts)
    assert all(
        contract.risk_free_interest is not None for contract in snapshot.contracts
    )


def test_public_risk_cache_marks_last_good_stale_then_expires_it():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    responses = fixture["responses"]
    failing = [False]

    def public_request(path):
        if failing[0]:
            raise RuntimeError("network down")
        if path.endswith("exchangeInfo"):
            return responses["exchangeInfo"]
        if path.endswith("mark"):
            return responses["mark"]
        return responses["index"]

    cache = RiskPublicCache(
        ttl_seconds=10,
        stale_max_seconds=60,
        public_request=public_request,
    )
    first = cache.fetch(now_ms=1_000)
    failing[0] = True
    stale = cache.fetch(now_ms=20_000)

    assert first.market.stale is False
    assert stale.market.stale is True
    with pytest.raises(BinancePrivateError) as expired:
        cache.fetch(now_ms=61_001)
    assert expired.value.issue_code is RiskIssueCode.STALE_DATA_EXPIRED
