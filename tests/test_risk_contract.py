from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from server.risk_types import (
    AccountConfigStatus,
    AccountEvaluationStatus,
    ApiErrorCode,
    LiquidationRiskEvaluateRequest,
    ReconciliationStatus,
    RiskAccountDetail,
    RiskAccountEvaluation,
    RiskIssue,
    RiskIssueCode,
    ScenarioStatus,
    TriggerDirection,
    TriggerReason,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "liquidation_risk"
SNAPSHOT_PATH = FIXTURE_DIR / "classic-options-snapshot.json"
VECTORS_PATH = FIXTURE_DIR / "risk-calculation-vectors.json"
FORBIDDEN_KEYS = {"apikey", "secretkey", "signature", "x-mbx-apikey"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def walk_keys(value: Any):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_keys(nested)


def as_finite_float(value: Any) -> float:
    parsed = float(value)
    assert math.isfinite(parsed)
    return parsed


def test_risk_contract_enums_are_frozen():
    assert [status.value for status in AccountConfigStatus] == [
        "configured",
        "missing_credentials",
        "configuration_error",
    ]
    assert [status.value for status in AccountEvaluationStatus] == [
        "ok",
        "unreconciled",
        "unsupported",
        "unavailable",
    ]
    assert [status.value for status in ReconciliationStatus] == [
        "matched",
        "warning",
        "unreconciled",
        "unsupported",
    ]
    assert [status.value for status in ScenarioStatus] == ["ok", "unavailable"]
    assert [reason.value for reason in TriggerReason] == [
        "margin_ratio_threshold",
        "non_positive_adjusted_equity",
        "negative_wallet_long_value_ratio",
    ]
    assert [direction.value for direction in TriggerDirection] == ["lower", "upper"]
    assert [code.value for code in ApiErrorCode] == [
        "invalid_request",
        "service_unavailable",
    ]
    assert [code.value for code in RiskIssueCode] == [
        "open_orders_not_included",
        "market_data_stale",
        "account_data_stale",
        "reconciliation_drift_warning",
        "expiry_settlement_approximation",
        "near_expiry_trigger_disabled",
        "expired_position_settled",
        "missing_credentials",
        "configuration_error",
        "upstream_unavailable",
        "stale_data_expired",
        "unsupported_margin_mode",
        "unsupported_quote_asset",
        "non_btc_open_position",
        "unreconciled_maintenance_margin",
        "missing_contract",
        "missing_mark",
        "invalid_numeric_value",
        "non_positive_scenario_volatility",
        "no_eligible_long_option_value",
        "invalid_json",
        "invalid_account_ids",
        "unknown_account_id",
        "invalid_valuation_time",
        "invalid_iv_shifts",
        "invalid_detail_account_id",
        "invalid_detail_iv_shift",
        "invalid_force_refresh",
    ]


def test_typed_dicts_freeze_request_and_partial_failure_shape():
    assert LiquidationRiskEvaluateRequest.__required_keys__ == {
        "accountIds",
        "valuationTimeMs",
        "ivShiftsVolPoints",
    }
    assert LiquidationRiskEvaluateRequest.__optional_keys__ == {
        "detailAccountId",
        "detailIvShiftVolPoints",
        "forceRefresh",
    }
    assert RiskIssue.__required_keys__ == {"code"}
    assert RiskIssue.__optional_keys__ == {"context"}
    assert RiskAccountEvaluation.__required_keys__ == {
        "id",
        "label",
        "status",
        "referenceIv",
        "error",
        "current",
        "reconciliation",
        "scenarios",
        "detail",
        "warnings",
    }
    assert RiskAccountDetail.__required_keys__ == {
        "ivShiftVolPoints",
        "breakEvenPrices",
        "remainingPositionCount",
        "curve",
        "maintenanceMarginContributions",
        "triggers",
        "warnings",
    }


def test_classic_options_fixture_is_synthetic_and_contains_no_secrets():
    fixture = load_json(SNAPSHOT_PATH)

    assert fixture["synthetic"] is True
    normalized_keys = {key.lower().replace("_", "").replace("-", "") for key in walk_keys(fixture)}
    assert not FORBIDDEN_KEYS.intersection(normalized_keys)
    assert set(fixture["responses"]) == {
        "time",
        "exchangeInfo",
        "mark",
        "index",
        "marginAccount",
        "position",
    }


def test_classic_options_fixture_has_complete_mixed_position_coverage():
    fixture = load_json(SNAPSHOT_PATH)
    responses = fixture["responses"]
    contracts = responses["exchangeInfo"]["optionSymbols"]
    marks = responses["mark"]
    positions = responses["position"]

    contract_by_symbol = {contract["symbol"]: contract for contract in contracts}
    mark_by_symbol = {mark["symbol"]: mark for mark in marks}
    position_symbols = {position["symbol"] for position in positions}

    assert len(positions) == fixture["expectedNormalized"]["positionCount"] == 6
    assert position_symbols == set(contract_by_symbol) == set(mark_by_symbol)
    assert {contract["side"] for contract in contracts} == {"CALL", "PUT"}
    assert {position["side"] for position in positions} == {"LONG", "SHORT"}
    assert len({contract["expiryDate"] for contract in contracts}) >= 3

    for contract in contracts:
        assert contract["underlying"] == "BTCUSDT"
        assert contract["quoteAsset"] == "USDT"
        assert contract["status"] == "TRADING"
        for field in (
            "strikePrice",
            "unit",
            "maintenanceMargin",
            "minMaintenanceMargin",
            "liquidationFeeRate",
        ):
            as_finite_float(contract[field])

    for mark in marks:
        for field in ("markPrice", "markIV", "riskFreeInterest"):
            as_finite_float(mark[field])

    for position in positions:
        quantity = as_finite_float(position["quantity"])
        assert (position["side"] == "LONG") is (quantity > 0)
        for field in ("markValue", "unrealizedPNL", "markPrice", "strikePrice"):
            as_finite_float(position[field])


def test_fixture_reconciles_current_maintenance_margin_and_derived_wallet():
    fixture = load_json(SNAPSHOT_PATH)
    responses = fixture["responses"]
    expected = fixture["expectedNormalized"]
    index_price = as_finite_float(responses["index"]["indexPrice"])
    contract_by_symbol = {
        contract["symbol"]: contract
        for contract in responses["exchangeInfo"]["optionSymbols"]
    }
    mark_by_symbol = {mark["symbol"]: mark for mark in responses["mark"]}

    model_maintenance_margin = 0.0
    long_option_value = 0.0
    source_times = [responses["marginAccount"]["time"]]
    for position in responses["position"]:
        source_times.append(position["time"])
        quantity = as_finite_float(position["quantity"])
        if quantity > 0:
            long_option_value += as_finite_float(position["markValue"])
            continue

        contract = contract_by_symbol[position["symbol"]]
        mark = mark_by_symbol[position["symbol"]]
        strike = as_finite_float(contract["strikePrice"])
        otm_amount = (
            min(index_price - strike, 0)
            if contract["side"] == "CALL"
            else min(strike - index_price, 0)
        )
        unit = as_finite_float(contract["unit"])
        per_contract = (
            max(
                index_price * as_finite_float(contract["minMaintenanceMargin"]),
                index_price * as_finite_float(contract["maintenanceMargin"])
                + otm_amount,
            )
            * unit
            + as_finite_float(mark["markPrice"])
            + index_price
            * unit
            * as_finite_float(contract["liquidationFeeRate"])
        )
        model_maintenance_margin += per_contract * abs(quantity)

    assert model_maintenance_margin == pytest_approx(expected["officialMaintenanceMargin"])
    assert long_option_value == pytest_approx(expected["currentLongOptionValue"])
    assert (
        expected["officialAdjustedEquity"] - long_option_value
        == pytest_approx(expected["derivedWalletBalance"])
    )
    assert min(source_times) == expected["currentSourceTimeMs"]
    assert max(source_times) - min(source_times) == expected["sourceSkewMs"]


def test_calculation_vectors_cover_frozen_formula_boundaries():
    vectors = load_json(VECTORS_PATH)

    assert vectors["numericConvention"]["yearMilliseconds"] == 365 * 24 * 60 * 60 * 1000
    assert vectors["timeBoundaries"] == {
        "expiredWhen": "valuationTimeMs >= expiryMs",
        "nearExpiryWhen": "expiryMs - 1800000 <= valuationTimeMs < expiryMs",
    }
    assert vectors["rootSearch"]["minimumSampleCount"] >= 2001
    assert vectors["rootSearch"]["boundaryToleranceUsdt"] == 1
    assert {case["expectedReason"] for case in vectors["triggerConditions"][:3]} == {
        reason.value for reason in TriggerReason
    }
    assert vectors["triggerConditions"][3]["expectedIssue"] == RiskIssueCode.NO_ELIGIBLE_LONG_OPTION_VALUE


def pytest_approx(value: float):
    return pytest.approx(value, rel=1e-12, abs=1e-9)
