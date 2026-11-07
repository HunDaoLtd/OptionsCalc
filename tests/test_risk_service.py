from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.risk_accounts import load_account_registry
from server.risk_binance import (
    BinancePrivateError,
    PrivateAccountSnapshot,
    normalize_risk_public_snapshot,
)
from server.risk_service import (
    LiquidationRiskService,
    RiskRequestError,
    RiskServiceError,
    _downsample_detail_curve,
    validate_evaluate_request,
)
from server.risk_types import RiskIssueCode


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "liquidation_risk"
    / "classic-options-snapshot.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def registry(include_missing: bool = False):
    account_ids = "main,income" if include_missing else "main"
    return load_account_registry(
        {
            "BINANCE_OPTIONS_ACCOUNT_IDS": account_ids,
            "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID": "main",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_LABEL": "Main",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_API_KEY": "fixture-key",
            "BINANCE_OPTIONS_ACCOUNT_MAIN_SECRET_KEY": "fixture-secret",
            "BINANCE_OPTIONS_ACCOUNT_INCOME_LABEL": "Income",
            "BINANCE_OPTIONS_ACCOUNT_INCOME_API_KEY": "",
            "BINANCE_OPTIONS_ACCOUNT_INCOME_SECRET_KEY": "",
        }
    )


def public_snapshot(*, contracts=None):
    responses = FIXTURE["responses"]
    snapshot = normalize_risk_public_snapshot(
        responses["exchangeInfo"],
        responses["mark"],
        responses["index"],
        fetched_at_ms=FIXTURE["capturedAtMs"],
    )
    if contracts is None:
        return snapshot
    return type(snapshot)(
        market=snapshot.market,
        contracts=tuple(contracts),
        fetched_at_ms=snapshot.fetched_at_ms,
    )


def private_snapshot(*, margin_account=None, positions=None, stale=False):
    responses = FIXTURE["responses"]
    return PrivateAccountSnapshot(
        margin_account=copy.deepcopy(
            responses["marginAccount"] if margin_account is None else margin_account
        ),
        positions=tuple(
            copy.deepcopy(responses["position"] if positions is None else positions)
        ),
        fetched_at_ms=FIXTURE["capturedAtMs"],
        stale=stale,
    )


class FakePublicCache:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot or public_snapshot()
        self.error = error

    def fetch(self):
        if self.error:
            raise self.error
        return self.snapshot


class FakePrivateCache:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot or private_snapshot()
        self.error = error
        self.calls: list[tuple[str, bool, int]] = []

    def fetch(self, account, *, force_refresh=False, now_ms=None):
        self.calls.append((account.account_id, force_refresh, now_ms))
        if self.error:
            raise self.error
        return self.snapshot


def valid_payload(**overrides):
    payload = {
        "accountIds": ["main"],
        "valuationTimeMs": FIXTURE["responses"]["time"]["serverTime"],
        "ivShiftsVolPoints": [0],
        "detailAccountId": "main",
        "detailIvShiftVolPoints": 0,
        "forceRefresh": False,
    }
    payload.update(overrides)
    return payload


def make_service(*, account_registry=None, public_cache=None, private_cache=None):
    return LiquidationRiskService(
        account_registry or registry(),
        public_cache or FakePublicCache(),
        private_cache or FakePrivateCache(),
        now_seconds=lambda: FIXTURE["capturedAtMs"] / 1000,
    )


def test_request_validation_locks_all_frozen_rules():
    parsed = validate_evaluate_request(valid_payload(), registry())
    assert parsed.account_ids == ("main",)
    assert parsed.iv_shifts_vol_points == (0.0,)

    full_chart_range = list(range(-50, 151, 10))
    assert validate_evaluate_request(
        valid_payload(ivShiftsVolPoints=full_chart_range), registry()
    ).iv_shifts_vol_points == tuple(float(value) for value in full_chart_range)

    invalid = valid_payload(
        accountIds=["main", "main", "unknown"],
        valuationTimeMs=1.5,
        ivShiftsVolPoints=[0, float("nan")],
        detailAccountId="unknown",
        detailIvShiftVolPoints=10,
        forceRefresh="yes",
    )
    with pytest.raises(RiskRequestError) as error:
        validate_evaluate_request(invalid, registry())
    assert {issue.code for issue in error.value.issues} == {
        RiskIssueCode.INVALID_ACCOUNT_IDS,
        RiskIssueCode.INVALID_DETAIL_ACCOUNT_ID,
        RiskIssueCode.INVALID_DETAIL_IV_SHIFT,
        RiskIssueCode.INVALID_FORCE_REFRESH,
        RiskIssueCode.INVALID_IV_SHIFTS,
        RiskIssueCode.INVALID_VALUATION_TIME,
    }


def test_fixture_evaluation_returns_matched_current_scenario_and_detail():
    private_cache = FakePrivateCache()
    service = make_service(private_cache=private_cache)

    result = service.evaluate(valid_payload(forceRefresh=True))

    assert result["market"]["indexPrice"] == 62885.16
    assert result["scan"] == {
        "minimum": pytest.approx(12577.032),
        "maximum": pytest.approx(125770.32),
        "sampleCount": 2001,
        "boundaryTolerance": 1.0,
    }
    account = result["accounts"][0]
    assert account["status"] == "ok"
    assert account["referenceIv"] == pytest.approx(0.586153846153846)
    assert account["error"] is None
    assert account["current"]["derivedWalletBalance"] == pytest.approx(
        FIXTURE["expectedNormalized"]["derivedWalletBalance"]
    )
    assert account["reconciliation"]["status"] == "matched"
    assert account["reconciliation"]["coveredPositions"] == 6
    assert account["reconciliation"]["totalPositions"] == 6
    assert account["reconciliation"]["mmDriftPercent"] == pytest.approx(0)
    assert account["scenarios"][0]["status"] == "ok"
    curve = account["detail"]["curve"]
    assert 350 <= len(curve) <= 425
    prices = [point["indexPrice"] for point in curve]
    assert prices == sorted(prices)
    assert prices[0] == pytest.approx(result["scan"]["minimum"])
    assert prices[-1] == pytest.approx(result["scan"]["maximum"])
    assert any(
        price == pytest.approx(result["market"]["indexPrice"])
        for price in prices
    )
    position_symbols = {
        item["symbol"] for item in FIXTURE["responses"]["position"]
    }
    required_strikes = {
        contract.strike_price
        for contract in public_snapshot().contracts
        if contract.symbol in position_symbols
        and result["scan"]["minimum"]
        <= contract.strike_price
        <= result["scan"]["maximum"]
    }
    assert required_strikes.issubset(set(prices))
    assert account["detail"]["breakEvenPrices"] is not None
    assert account["detail"]["breakEvenPrices"] == sorted(
        account["detail"]["breakEvenPrices"]
    )
    assert account["detail"]["remainingPositionCount"] == 6
    assert len(account["detail"]["maintenanceMarginContributions"]) == 4
    entry_price_by_symbol = {
        item["symbol"]: float(item["entryPrice"])
        for item in FIXTURE["responses"]["position"]
    }
    assert all(
        item["entryPrice"] == pytest.approx(entry_price_by_symbol[item["symbol"]])
        for item in account["detail"]["maintenanceMarginContributions"]
    )
    assert private_cache.calls[0][1] is True
    serialized = json.dumps(result)
    assert len(serialized.encode("utf-8")) < 100_000
    assert "fixture-key" not in serialized
    assert "fixture-secret" not in serialized


def test_full_iv_chart_range_returns_in_request_order_with_isolated_failures():
    shifts = list(range(-50, 151, 10))
    payload = valid_payload(ivShiftsVolPoints=shifts)
    payload.pop("detailAccountId")
    payload.pop("detailIvShiftVolPoints")
    result = make_service().evaluate(payload)

    scenarios = result["accounts"][0]["scenarios"]
    assert [scenario["ivShiftVolPoints"] for scenario in scenarios] == [
        float(shift) for shift in shifts
    ]
    assert scenarios[0]["status"] == "ok"
    assert next(
        scenario for scenario in scenarios if scenario["ivShiftVolPoints"] == 0
    )["status"] == "ok"
    assert next(
        scenario for scenario in scenarios if scenario["ivShiftVolPoints"] == 150
    )["status"] == "ok"


def test_detail_curve_downsampling_preserves_required_and_transition_neighbors():
    curve = tuple(
        SimpleNamespace(
            index_price=float(index),
            triggered=index >= 500,
            trigger_reason=("threshold" if index >= 500 else None),
            margin_ratio=index / 1000,
        )
        for index in range(1000)
    )

    sampled = _downsample_detail_curve(
        curve,
        mandatory_prices=(50.0, 750.5),
        target_points=20,
    )
    prices = {point.index_price for point in sampled}

    assert len(sampled) == 20
    assert [point.index_price for point in sampled] == sorted(prices)
    assert {0.0, 49.0, 50.0, 499.0, 500.0, 750.0, 751.0, 999.0}.issubset(
        prices
    )


def test_negative_short_mark_value_is_normalized_as_unsigned_market_value():
    positions = copy.deepcopy(FIXTURE["responses"]["position"])
    for position in positions:
        if float(position["quantity"]) < 0:
            position["markValue"] = str(-abs(float(position["markValue"])))
    service = make_service(
        private_cache=FakePrivateCache(snapshot=private_snapshot(positions=positions))
    )

    account = service.evaluate(valid_payload())["accounts"][0]

    assert account["status"] == "ok"
    assert account["reconciliation"]["coveredPositions"] == 6
    assert account["current"]["derivedWalletBalance"] == pytest.approx(
        FIXTURE["expectedNormalized"]["derivedWalletBalance"]
    )


def test_reconciliation_accepts_live_api_margin_without_liquidation_reserve():
    public = public_snapshot()
    liquidation_reserve = sum(
        public.market.index_price
        * contract.unit
        * contract.liquidation_fee_rate
        * abs(float(position["quantity"]))
        for contract in public.contracts
        for position in FIXTURE["responses"]["position"]
        if contract.symbol == position["symbol"]
        and float(position["quantity"]) < 0
        and contract.unit is not None
        and contract.liquidation_fee_rate is not None
    )
    margin = copy.deepcopy(FIXTURE["responses"]["marginAccount"])
    margin["asset"][0]["maintMargin"] = str(
        FIXTURE["expectedNormalized"]["officialMaintenanceMargin"]
        - liquidation_reserve
    )
    service = make_service(
        public_cache=FakePublicCache(snapshot=public),
        private_cache=FakePrivateCache(snapshot=private_snapshot(margin_account=margin)),
    )

    reconciliation = service.evaluate(valid_payload())["accounts"][0][
        "reconciliation"
    ]

    assert reconciliation["status"] == "matched"
    assert reconciliation["mmDriftPercent"] == pytest.approx(0)


@pytest.mark.parametrize(
    ("multiplier", "expected_account_status", "expected_reconciliation"),
    [
        (1.01, "ok", "warning"),
        (1.10, "unreconciled", "unreconciled"),
    ],
)
def test_reconciliation_warning_and_unreconciled_thresholds(
    multiplier, expected_account_status, expected_reconciliation
):
    margin = copy.deepcopy(FIXTURE["responses"]["marginAccount"])
    margin["asset"][0]["maintMargin"] = str(
        FIXTURE["expectedNormalized"]["officialMaintenanceMargin"] * multiplier
    )
    service = make_service(
        private_cache=FakePrivateCache(snapshot=private_snapshot(margin_account=margin))
    )

    account = service.evaluate(valid_payload())["accounts"][0]

    assert account["status"] == expected_account_status
    assert account["reconciliation"]["status"] == expected_reconciliation
    if expected_account_status == "unreconciled":
        assert account["scenarios"] is None
        assert account["detail"] is None


def test_missing_contract_hides_scenarios_but_preserves_current_metrics():
    public = public_snapshot()
    service = make_service(
        public_cache=FakePublicCache(
            snapshot=public_snapshot(contracts=public.contracts[:-1])
        )
    )

    account = service.evaluate(valid_payload())["accounts"][0]

    assert account["status"] == "unreconciled"
    assert account["current"] is not None
    assert account["reconciliation"]["coveredPositions"] == 5
    assert account["scenarios"] is None


def test_non_btc_position_returns_unsupported_without_fake_scenarios():
    positions = copy.deepcopy(FIXTURE["responses"]["position"])
    positions.append(
        {
            "symbol": "ETH-260925-3000-P",
            "side": "LONG",
            "quantity": "1",
            "markValue": "10",
            "unrealizedPNL": "0",
            "quoteAsset": "USDT",
            "time": FIXTURE["capturedAtMs"],
        }
    )
    service = make_service(
        private_cache=FakePrivateCache(snapshot=private_snapshot(positions=positions))
    )

    account = service.evaluate(valid_payload())["accounts"][0]

    assert account["status"] == "unsupported"
    assert account["error"]["code"] == "non_btc_open_position"
    assert account["current"] is not None
    assert account["scenarios"] is None


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("quote", "unsupported_quote_asset"),
        ("margin_mode", "unsupported_margin_mode"),
    ],
)
def test_non_usdt_and_non_classic_accounts_hide_unreliable_current(
    mutation, expected_code
):
    margin = copy.deepcopy(FIXTURE["responses"]["marginAccount"])
    positions = copy.deepcopy(FIXTURE["responses"]["position"])
    if mutation == "quote":
        positions[0]["quoteAsset"] = "BUSD"
    else:
        margin["marginMode"] = "PORTFOLIO"
    service = make_service(
        private_cache=FakePrivateCache(
            snapshot=private_snapshot(margin_account=margin, positions=positions)
        )
    )

    account = service.evaluate(valid_payload())["accounts"][0]

    assert account["status"] == "unsupported"
    assert account["error"]["code"] == expected_code
    assert account["current"] is None
    assert account["scenarios"] is None


def test_stale_private_snapshot_keeps_source_time_and_adds_typed_warning():
    service = make_service(
        private_cache=FakePrivateCache(snapshot=private_snapshot(stale=True))
    )

    account = service.evaluate(valid_payload())["accounts"][0]

    assert account["current"]["stale"] is True
    assert {warning["code"] for warning in account["warnings"]} == {
        "account_data_stale"
    }


def test_multi_account_partial_failure_is_preserved_in_200_response():
    service = make_service(account_registry=registry(include_missing=True))
    payload = valid_payload(
        accountIds=["main", "income"],
        detailAccountId="main",
    )

    result = service.evaluate(payload)

    assert [account["id"] for account in result["accounts"]] == ["main", "income"]
    assert [account["status"] for account in result["accounts"]] == [
        "ok",
        "unavailable",
    ]
    assert result["accounts"][1]["error"]["code"] == "missing_credentials"
    assert result["accounts"][1]["current"] is None


def test_all_private_or_public_upstream_failures_return_service_error():
    private_error = BinancePrivateError(
        RiskIssueCode.UPSTREAM_UNAVAILABLE, "/eapi/v1/marginAccount"
    )
    with pytest.raises(RiskServiceError):
        make_service(private_cache=FakePrivateCache(error=private_error)).evaluate(
            valid_payload()
        )

    public_error = BinancePrivateError(
        RiskIssueCode.UPSTREAM_UNAVAILABLE, "public_risk_snapshot"
    )
    with pytest.raises(RiskServiceError):
        make_service(public_cache=FakePublicCache(error=public_error)).evaluate(
            valid_payload()
        )
