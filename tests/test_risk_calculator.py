from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
import server.risk_calculator as risk_calculator

from server.risk_calculator import (
    DEFAULT_BOUNDARY_TOLERANCE,
    DEFAULT_SCAN_SAMPLE_COUNT,
    NEAR_EXPIRY_MS,
    YEAR_MS,
    analyze_scenario,
    analyze_scenarios,
    black_scholes_prices,
    calculate_current_account,
    calculate_scenario_adjusted_equity,
    derive_wallet_balance,
    evaluate_liquidation_trigger,
    evaluate_scenario_point,
    find_trigger_boundaries,
    option_otm_amount,
    position_maintenance_margin,
    settle_expired_position,
)
from server.risk_types import (
    OptionSide,
    PositionSide,
    RiskAccountSnapshot,
    RiskContract,
    RiskEvaluationInput,
    RiskIssueCode,
    RiskMarketSnapshot,
    RiskPosition,
    ScenarioPointCalculation,
    ScenarioStatus,
    TriggerDirection,
    TriggerReason,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "liquidation_risk"
VECTORS = json.loads(
    (FIXTURE_DIR / "risk-calculation-vectors.json").read_text(encoding="utf-8")
)
CLASSIC_FIXTURE = json.loads(
    (FIXTURE_DIR / "classic-options-snapshot.json").read_text(encoding="utf-8")
)


def make_contract(
    *,
    symbol: str = "BTC-260829-62000-P",
    option_side: OptionSide = OptionSide.PUT,
    expiry_ms: int = 1787990400000,
    strike_price: float = 62000,
    unit: float | None = 1,
    maintenance_margin_rate: float | None = 0.075,
    min_maintenance_margin_rate: float | None = 0.05,
    liquidation_fee_rate: float | None = 0.0019,
    mark_price: float | None = 2100,
    mark_iv: float | None = 0.65,
    risk_free_interest: float | None = 0.03,
) -> RiskContract:
    return RiskContract(
        symbol=symbol,
        underlying="BTCUSDT",
        quote_asset="USDT",
        option_side=option_side,
        expiry_ms=expiry_ms,
        strike_price=strike_price,
        unit=unit,
        maintenance_margin_rate=maintenance_margin_rate,
        min_maintenance_margin_rate=min_maintenance_margin_rate,
        liquidation_fee_rate=liquidation_fee_rate,
        mark_price=mark_price,
        mark_iv=mark_iv,
        risk_free_interest=risk_free_interest,
    )


def classic_evaluation_input(
    *,
    valuation_time_ms: int = 1785542400000,
    iv_shift_vol_points: float = 0,
) -> RiskEvaluationInput:
    responses = CLASSIC_FIXTURE["responses"]
    mark_by_symbol = {mark["symbol"]: mark for mark in responses["mark"]}
    contracts = tuple(
        RiskContract(
            symbol=item["symbol"],
            underlying=item["underlying"],
            quote_asset=item["quoteAsset"],
            option_side=OptionSide(item["side"]),
            expiry_ms=item["expiryDate"],
            strike_price=float(item["strikePrice"]),
            unit=float(item["unit"]),
            maintenance_margin_rate=float(item["maintenanceMargin"]),
            min_maintenance_margin_rate=float(item["minMaintenanceMargin"]),
            liquidation_fee_rate=float(item["liquidationFeeRate"]),
            mark_price=float(mark_by_symbol[item["symbol"]]["markPrice"]),
            mark_iv=float(mark_by_symbol[item["symbol"]]["markIV"]),
            risk_free_interest=float(
                mark_by_symbol[item["symbol"]]["riskFreeInterest"]
            ),
        )
        for item in responses["exchangeInfo"]["optionSymbols"]
    )
    positions = tuple(
        RiskPosition(
            symbol=item["symbol"],
            position_side=PositionSide(item["side"]),
            quantity=float(item["quantity"]),
            entry_price=float(item["entryPrice"]),
            mark_value=float(item["markValue"]),
            unrealized_pnl=float(item["unrealizedPNL"]),
            source_time_ms=item["time"],
        )
        for item in responses["position"]
    )
    account_asset = responses["marginAccount"]["asset"][0]
    index_price = float(responses["index"]["indexPrice"])
    return RiskEvaluationInput(
        market=RiskMarketSnapshot(
            index_price=index_price,
            source_time_ms=responses["index"]["time"],
            stale=False,
        ),
        account=RiskAccountSnapshot(
            account_id="fixture",
            label="Fixture",
            official_adjusted_equity=float(account_asset["adjustedEquity"]),
            official_maintenance_margin=float(account_asset["maintMargin"]),
            unrealized_pnl=float(account_asset["unrealizedPNL"]),
            positions=positions,
            source_time_ms=min(position.source_time_ms for position in positions),
            stale=False,
        ),
        contracts=contracts,
        valuation_time_ms=valuation_time_ms,
        iv_shift_vol_points=iv_shift_vol_points,
        scan_minimum=index_price * 0.2,
        scan_maximum=index_price * 2.0,
    )


def trigger_point(
    price: float,
    triggered: bool,
    reason: TriggerReason = TriggerReason.MARGIN_RATIO_THRESHOLD,
) -> ScenarioPointCalculation:
    return ScenarioPointCalculation(
        available=True,
        index_price=price,
        adjusted_equity=100,
        maintenance_margin=95 if triggered else 50,
        margin_ratio=0.95 if triggered else 0.5,
        derived_wallet_balance=100,
        eligible_long_option_value=0,
        settlement_cashflow=0,
        triggered=triggered,
        trigger_reason=reason if triggered else None,
    )


def state_evaluator(predicate):
    return lambda price: trigger_point(price, predicate(price))


def pnl_fast_point(price: float, pnl: float | None):
    return risk_calculator._FastScenarioPoint(
        available=pnl is not None,
        index_price=price,
        adjusted_equity=100 if pnl is not None else None,
        maintenance_margin=50 if pnl is not None else None,
        margin_ratio=0.5 if pnl is not None else None,
        eligible_long_option_value=0 if pnl is not None else None,
        settlement_cashflow=0 if pnl is not None else None,
        triggered=False if pnl is not None else None,
        trigger_reason=None,
        unrealized_pnl=pnl,
    )


def pnl_root_search(function, nodes=range(10, 101)):
    curve = tuple(pnl_fast_point(float(price), function(float(price))) for price in nodes)
    return risk_calculator._fast_search_pnl_roots(
        curve,
        lambda price: pnl_fast_point(price, function(price)),
        tolerance=DEFAULT_BOUNDARY_TOLERANCE,
    )


def test_pnl_root_search_finds_sorts_and_deduplicates_all_crossings():
    result = pnl_root_search(
        lambda price: (price - 30.25) * (price - 50.5) * (price - 80.75)
    )

    assert result.available is True
    assert result.prices == tuple(sorted(result.prices))
    assert result.prices == pytest.approx(
        (30.25, 50.5, 80.75),
        abs=DEFAULT_BOUNDARY_TOLERANCE,
    )

    deduplicated = pnl_root_search(
        lambda price: (price - 40) * (price - 40.5),
        nodes=(10, 39, 40, 40.5, 41, 100),
    )
    assert deduplicated.prices == pytest.approx((40.25,))


def test_pnl_root_search_handles_single_exact_root_no_root_and_unavailable_pnl():
    exact = pnl_root_search(lambda price: price - 40)
    no_root = pnl_root_search(lambda price: price * price + 1)
    unavailable = pnl_root_search(
        lambda price: None if price == 60 else price - 40
    )

    assert exact.prices == (40,)
    assert no_root.available is True and no_root.prices == ()
    assert unavailable.available is False and unavailable.prices == ()


def test_pnl_root_search_detects_a_tangent_zero_without_a_sign_change():
    result = pnl_root_search(lambda price: (price - 60.25) ** 2)

    assert result.available is True
    assert result.prices == pytest.approx(
        (60.25,),
        abs=DEFAULT_BOUNDARY_TOLERANCE,
    )


def test_black_scholes_matches_frozen_vector_and_put_call_parity():
    vector = VECTORS["blackScholes"][0]
    result = black_scholes_prices(
        vector["input"]["indexPrice"],
        vector["input"]["strikePrice"],
        vector["input"]["timeYears"],
        vector["input"]["riskFreeInterest"],
        vector["input"]["volatility"],
        vector["input"]["unit"],
    )

    assert result.d1 == pytest.approx(vector["expected"]["d1"])
    assert result.d2 == pytest.approx(vector["expected"]["d2"])
    assert result.call_price == pytest.approx(vector["expected"]["callPrice"])
    assert result.put_price == pytest.approx(vector["expected"]["putPrice"])
    parity = 100 - 100 * math.exp(-0.05)
    assert result.call_price - result.put_price == pytest.approx(parity)

    scaled = black_scholes_prices(100, 100, 1, 0.05, 0.2, 0.1)
    assert scaled.call_price == pytest.approx(result.call_price * 0.1)
    assert scaled.put_price == pytest.approx(result.put_price * 0.1)
    with pytest.raises(FrozenInstanceError):
        result.d1 = 0


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ((100, 100, 0, 0.05, 0.2, 1), "time_years"),
        ((100, 100, 1, 0.05, 0, 1), "volatility"),
        ((float("nan"), 100, 1, 0.05, 0.2, 1), "index_price"),
        ((100, 100, 1, 0.05, 0.2, True), "unit"),
    ],
)
def test_black_scholes_rejects_invalid_inputs(arguments, field):
    with pytest.raises(ValueError, match=field):
        black_scholes_prices(*arguments)


def test_black_scholes_normalizes_arithmetic_failures_to_value_error():
    with pytest.raises(ValueError, match="Black-Scholes"):
        black_scholes_prices(100, 100, 5e-324, 0.05, 5e-324, 1)


def test_otm_amount_matches_frozen_signed_vectors():
    for vector in VECTORS["otmAmount"]:
        assert option_otm_amount(
            vector["optionSide"],
            vector["indexPrice"],
            vector["strikePrice"],
        ) == pytest.approx(vector["expected"])


def test_maintenance_margin_uses_signed_quantity_unit_and_dynamic_rates():
    vector = VECTORS["maintenanceMargin"][0]
    values = vector["input"]
    contract = make_contract(
        option_side=OptionSide(values["optionSide"]),
        strike_price=values["strikePrice"],
        unit=values["unit"],
        maintenance_margin_rate=values["maintenanceMarginRate"],
        min_maintenance_margin_rate=values["minMaintenanceMarginRate"],
        liquidation_fee_rate=values["liquidationFeeRate"],
    )

    result = position_maintenance_margin(
        contract,
        values["indexPrice"],
        values["optionPrice"],
        values["quantity"],
    )
    assert result == pytest.approx(vector["expected"]["maintenanceMargin"])
    assert position_maintenance_margin(contract, 62885.16, 650, 1.2) == 0

    higher_rate_contract = replace(contract, min_maintenance_margin_rate=0.08)
    assert position_maintenance_margin(
        higher_rate_contract, 62885.16, 650, -1.2
    ) > result


def test_adjusted_equity_helpers_match_frozen_vectors():
    derived_vector, scenario_vector = VECTORS["adjustedEquity"]
    derived = derive_wallet_balance(
        derived_vector["officialAdjustedEquity"],
        derived_vector["currentLongPositionMarkValues"],
    )
    assert derived == pytest.approx(derived_vector["expectedDerivedWalletBalance"])
    assert calculate_scenario_adjusted_equity(
        scenario_vector["derivedWalletBalance"],
        scenario_vector["scenarioLongOptionValues"],
        scenario_vector["expiredPositionSettlementCashflows"],
    ) == pytest.approx(scenario_vector["expectedScenarioAdjustedEquity"])


def test_settlement_matches_itm_vectors_and_otm_fee_is_zero():
    for vector in VECTORS["settlement"]:
        values = vector["input"]
        contract = make_contract(
            option_side=OptionSide(values["optionSide"]),
            strike_price=values["strikePrice"],
            unit=values["unit"],
        )
        result = settle_expired_position(
            contract, values["settlementPrice"], values["quantity"]
        )
        assert result.payoff_per_contract == pytest.approx(
            vector["expected"]["payoffPerContract"]
        )
        assert result.exercise_fee == pytest.approx(
            vector["expected"]["exerciseFee"]
        )
        assert result.settlement_cashflow == pytest.approx(
            vector["expected"]["settlementCashflow"]
        )

    otm_call = make_contract(option_side=OptionSide.CALL, strike_price=70000)
    result = settle_expired_position(otm_call, 60000, -0.5)
    assert result.payoff_per_contract == 0
    assert result.exercise_fee == 0
    assert result.settlement_cashflow == 0


def test_trigger_evaluation_matches_all_frozen_conditions():
    for vector in VECTORS["triggerConditions"]:
        result = evaluate_liquidation_trigger(
            vector["input"]["maintenanceMargin"],
            vector["input"]["adjustedEquity"],
            vector["input"]["derivedWalletBalance"],
            vector["input"]["eligibleLongOptionValue"],
        )
        assert result.triggered == vector["expectedTriggered"]
        if "expectedReason" in vector:
            assert result.reason is TriggerReason(vector["expectedReason"])
        else:
            assert result.issue is not None
            assert result.issue.code is RiskIssueCode(vector["expectedIssue"])

    exact_threshold = evaluate_liquidation_trigger(95, 100, 5, 95)
    assert exact_threshold.triggered is True
    assert exact_threshold.margin_ratio == pytest.approx(0.95)
    non_positive = evaluate_liquidation_trigger(1, -1, -1, 0)
    assert non_positive.margin_ratio is None


def test_current_account_recomputes_fixture_mm_and_uses_official_trigger_values():
    evaluation_input = classic_evaluation_input()
    expected = CLASSIC_FIXTURE["expectedNormalized"]
    result = calculate_current_account(evaluation_input)

    assert result.available is True
    assert result.derived_wallet_balance == pytest.approx(
        expected["derivedWalletBalance"]
    )
    assert result.long_option_value == pytest.approx(
        expected["currentLongOptionValue"]
    )
    assert result.model_maintenance_margin == pytest.approx(
        expected["officialMaintenanceMargin"]
    )
    assert result.margin_ratio == pytest.approx(0.728)
    assert result.trigger is not None and result.trigger.triggered is False
    assert len(result.contributions) == 4
    assert {row.entry_price for row in result.contributions} == {
        float(item["entryPrice"])
        for item in CLASSIC_FIXTURE["responses"]["position"]
        if float(item["quantity"]) < 0
    }
    assert sum(row.maintenance_margin_share for row in result.contributions) == pytest.approx(1)
    assert list(result.contributions) == sorted(
        result.contributions,
        key=lambda row: (-row.maintenance_margin, row.symbol),
    )


def test_current_account_marks_official_current_trigger_separately():
    evaluation_input = classic_evaluation_input()
    triggered_account = replace(
        evaluation_input.account,
        official_adjusted_equity=100,
        official_maintenance_margin=95,
    )
    result = calculate_current_account(replace(evaluation_input, account=triggered_account))
    assert result.available is True
    assert result.trigger is not None
    assert result.trigger.triggered is True
    assert result.trigger.reason is TriggerReason.MARGIN_RATIO_THRESHOLD


def test_scenario_point_values_all_unexpired_positions_with_bsm():
    evaluation_input = classic_evaluation_input()
    point = evaluate_scenario_point(evaluation_input, 62885.16)

    assert point.available is True
    assert point.adjusted_equity is not None
    assert point.maintenance_margin is not None and point.maintenance_margin > 0
    assert point.eligible_long_option_value is not None
    assert point.eligible_long_option_value > 0
    assert point.settlement_cashflow == 0
    expected_value_change = 0.0
    contracts = {contract.symbol: contract for contract in evaluation_input.contracts}
    for position in evaluation_input.account.positions:
        contract = contracts[position.symbol]
        prices = black_scholes_prices(
            62885.16,
            contract.strike_price,
            (contract.expiry_ms - evaluation_input.valuation_time_ms) / YEAR_MS,
            contract.risk_free_interest,
            contract.mark_iv,
            contract.unit,
        )
        option_price = (
            prices.call_price
            if contract.option_side is OptionSide.CALL
            else prices.put_price
        )
        expected_value_change += (
            option_price - contract.mark_price
        ) * position.quantity
    assert point.unrealized_pnl == pytest.approx(
        evaluation_input.account.unrealized_pnl + expected_value_change
    )
    assert len(point.contributions) == 4
    assert point.issues == ()

    shifted = evaluate_scenario_point(
        replace(
            evaluation_input,
            valuation_time_ms=evaluation_input.valuation_time_ms + 24 * 60 * 60 * 1000,
            iv_shift_vol_points=10,
        ),
        62885.16,
    )
    assert shifted.available is True
    assert shifted.unrealized_pnl != pytest.approx(point.unrealized_pnl)


def test_scenario_point_settles_one_then_multiple_expiries():
    one_expiry_input = classic_evaluation_input(valuation_time_ms=1785657600000)
    one_expiry = evaluate_scenario_point(one_expiry_input, 50000)
    assert one_expiry.available is True
    assert one_expiry.settlement_cashflow is not None
    assert one_expiry.settlement_cashflow < 0
    assert len(one_expiry.contributions) == 3
    assert {issue.code for issue in one_expiry.issues} == {
        RiskIssueCode.EXPIRED_POSITION_SETTLED,
        RiskIssueCode.EXPIRY_SETTLEMENT_APPROXIMATION,
    }

    multiple_expiry_input = classic_evaluation_input(
        valuation_time_ms=1786176000000
    )
    multiple_expiry = evaluate_scenario_point(multiple_expiry_input, 70000)
    assert multiple_expiry.available is True
    assert len(multiple_expiry.contributions) == 2
    expired_symbols = {
        dict(issue.context)["symbol"]
        for issue in multiple_expiry.issues
        if issue.code is RiskIssueCode.EXPIRED_POSITION_SETTLED
    }
    assert expired_symbols == {
        "BTC-260802-60000-P",
        "BTC-260808-65000-C",
    }


def test_scenario_pnl_excludes_expired_positions_and_keeps_remaining_official_baseline():
    valuation_time_ms = 1786176000000
    evaluation_input = classic_evaluation_input(
        valuation_time_ms=valuation_time_ms
    )
    spot = 70000

    point = evaluate_scenario_point(evaluation_input, spot)

    contracts = {contract.symbol: contract for contract in evaluation_input.contracts}
    expired_positions = [
        position
        for position in evaluation_input.account.positions
        if contracts[position.symbol].expiry_ms <= valuation_time_ms
    ]
    remaining_positions = [
        position
        for position in evaluation_input.account.positions
        if contracts[position.symbol].expiry_ms > valuation_time_ms
    ]
    expected_pnl = evaluation_input.account.unrealized_pnl - sum(
        position.unrealized_pnl for position in expired_positions
    )
    for position in remaining_positions:
        contract = contracts[position.symbol]
        prices = black_scholes_prices(
            spot,
            contract.strike_price,
            (contract.expiry_ms - valuation_time_ms) / YEAR_MS,
            contract.risk_free_interest,
            contract.mark_iv,
            contract.unit,
        )
        option_price = (
            prices.call_price
            if contract.option_side is OptionSide.CALL
            else prices.put_price
        )
        expected_pnl += (
            option_price - contract.mark_price
        ) * position.quantity

    assert point.available is True
    assert point.settlement_cashflow != 0
    assert point.unrealized_pnl == pytest.approx(expected_pnl)


def test_expired_cashflow_does_not_change_the_frozen_derived_wallet_trigger_input():
    contract = make_contract(
        symbol="BTC-TEST-100-C",
        option_side=OptionSide.CALL,
        expiry_ms=1_000,
        strike_price=100,
        mark_price=20,
    )
    position = RiskPosition(
        symbol=contract.symbol,
        position_side=PositionSide.LONG,
        quantity=1,
        entry_price=15,
        mark_value=20,
        unrealized_pnl=0,
        source_time_ms=900,
    )
    evaluation_input = RiskEvaluationInput(
        market=RiskMarketSnapshot(index_price=120, source_time_ms=1_000, stale=False),
        account=RiskAccountSnapshot(
            account_id="test",
            label="Test",
            official_adjusted_equity=10,
            official_maintenance_margin=0,
            unrealized_pnl=0,
            positions=(position,),
            source_time_ms=900,
            stale=False,
        ),
        contracts=(contract,),
        valuation_time_ms=1_000,
        iv_shift_vol_points=0,
        scan_minimum=24,
        scan_maximum=240,
    )

    point = evaluate_scenario_point(evaluation_input, 120)

    assert point.available is False
    assert RiskIssueCode.NO_ELIGIBLE_LONG_OPTION_VALUE in {
        issue.code for issue in point.issues
    }

    funded_input = replace(
        evaluation_input,
        account=replace(evaluation_input.account, official_adjusted_equity=100),
    )
    funded_point = evaluate_scenario_point(funded_input, 120)
    expected_fee = min(0.00015 * 120, 0.10 * 20)
    assert funded_point.available is True
    assert funded_point.settlement_cashflow == pytest.approx(20 - expected_fee)
    assert funded_point.unrealized_pnl == 0


def test_near_expiry_boundary_is_inclusive_and_expiry_switches_to_settlement():
    expiry_ms = 1785657600000
    at_boundary = classic_evaluation_input(
        valuation_time_ms=expiry_ms - NEAR_EXPIRY_MS
    )
    unavailable = analyze_scenario(at_boundary)
    assert unavailable.status is ScenarioStatus.UNAVAILABLE
    assert unavailable.curve == ()
    assert RiskIssueCode.NEAR_EXPIRY_TRIGGER_DISABLED in {
        issue.code for issue in unavailable.issues
    }

    before_boundary = evaluate_scenario_point(
        classic_evaluation_input(
            valuation_time_ms=expiry_ms - NEAR_EXPIRY_MS - 1
        ),
        62885.16,
    )
    assert before_boundary.available is True

    at_expiry = evaluate_scenario_point(
        classic_evaluation_input(valuation_time_ms=expiry_ms),
        62885.16,
    )
    assert at_expiry.available is True
    assert RiskIssueCode.EXPIRED_POSITION_SETTLED in {
        issue.code for issue in at_expiry.issues
    }


def test_composite_entry_returns_typed_issues_for_missing_data_and_iv():
    evaluation_input = classic_evaluation_input()
    missing_mark_contract = replace(evaluation_input.contracts[0], mark_price=None)
    missing_mark = analyze_scenario(
        replace(
            evaluation_input,
            contracts=(missing_mark_contract, *evaluation_input.contracts[1:]),
        )
    )
    assert missing_mark.status is ScenarioStatus.UNAVAILABLE
    assert RiskIssueCode.MISSING_MARK in {issue.code for issue in missing_mark.issues}

    missing_contract = analyze_scenario(
        replace(evaluation_input, contracts=evaluation_input.contracts[1:])
    )
    assert missing_contract.status is ScenarioStatus.UNAVAILABLE
    assert RiskIssueCode.MISSING_CONTRACT in {
        issue.code for issue in missing_contract.issues
    }

    missing_contract_parameter = analyze_scenario(
        replace(
            evaluation_input,
            contracts=(
                replace(evaluation_input.contracts[0], unit=None),
                *evaluation_input.contracts[1:],
            ),
        )
    )
    assert missing_contract_parameter.status is ScenarioStatus.UNAVAILABLE
    assert RiskIssueCode.MISSING_CONTRACT in {
        issue.code for issue in missing_contract_parameter.issues
    }

    non_positive_iv = analyze_scenario(
        replace(evaluation_input, iv_shift_vol_points=-55)
    )
    assert non_positive_iv.status is ScenarioStatus.UNAVAILABLE
    assert RiskIssueCode.NON_POSITIVE_SCENARIO_VOLATILITY in {
        issue.code for issue in non_positive_iv.issues
    }


def test_composite_entry_aggregates_and_sorts_independent_input_issues():
    evaluation_input = classic_evaluation_input()
    invalid_position = replace(
        evaluation_input.account.positions[0],
        quantity=1,
        mark_value=-1,
        unrealized_pnl=float("nan"),
    )
    invalid_input = replace(
        evaluation_input,
        market=replace(evaluation_input.market, index_price=0),
        account=replace(
            evaluation_input.account,
            official_adjusted_equity=float("nan"),
            official_maintenance_margin=-1,
            positions=(invalid_position, *evaluation_input.account.positions[1:]),
        ),
    )

    result = calculate_current_account(invalid_input)
    fields = [dict(issue.context)["field"] for issue in result.issues]

    assert result.available is False
    assert fields == sorted(fields)
    assert fields == [
        "mark_value",
        "market.index_price",
        "official_adjusted_equity",
        "official_maintenance_margin",
        "quantity_side",
        "unrealized_pnl",
    ]

    invalid_scenario = analyze_scenario(
        replace(
            evaluation_input,
            valuation_time_ms=-1,
            iv_shift_vol_points=float("nan"),
        )
    )
    assert invalid_scenario.status is ScenarioStatus.UNAVAILABLE
    assert invalid_scenario.iv_shift_vol_points is None
    assert [dict(issue.context)["field"] for issue in invalid_scenario.issues] == [
        "iv_shift_vol_points",
        "valuation_time_ms",
    ]


@pytest.mark.parametrize(
    ("predicate", "expected_prices", "expected_directions"),
    [
        (
            lambda price: price <= 40,
            [40],
            [TriggerDirection.LOWER],
        ),
        (
            lambda price: price >= 80,
            [80],
            [TriggerDirection.UPPER],
        ),
        (
            lambda price: price <= 40 or price >= 80,
            [40, 80],
            [TriggerDirection.LOWER, TriggerDirection.UPPER],
        ),
    ],
)
def test_root_search_finds_lower_upper_and_two_sided_bounds(
    predicate, expected_prices, expected_directions
):
    result = find_trigger_boundaries(
        state_evaluator(predicate),
        minimum=20,
        maximum=100,
        current_index=60,
    )
    assert result.available is True
    assert len(result.curve) >= DEFAULT_SCAN_SAMPLE_COUNT
    assert [trigger.direction for trigger in result.triggers] == expected_directions
    assert [trigger.price for trigger in result.triggers] == pytest.approx(
        expected_prices, abs=DEFAULT_BOUNDARY_TOLERANCE
    )


def test_root_search_finds_multiple_crossings_and_refines_to_one_usdt():
    predicate = lambda price: (
        price <= 30 or 40 <= price <= 50 or price >= 80
    )
    result = find_trigger_boundaries(
        state_evaluator(predicate),
        minimum=10,
        maximum=100,
        current_index=60,
        extra_nodes=(30, 40, 50, 80, 80 + 1e-12),
    )
    assert result.available is True
    assert len(result.triggers) == 4
    assert [trigger.price for trigger in result.triggers] == pytest.approx(
        [30, 40, 50, 80], abs=DEFAULT_BOUNDARY_TOLERANCE
    )
    assert any(
        point.index_price is not None and abs(point.index_price - 40) <= 1e-9
        for point in result.curve
    )


def test_root_search_handles_no_crossing_and_currently_triggered_curve():
    no_crossing = find_trigger_boundaries(
        state_evaluator(lambda _price: False),
        minimum=20,
        maximum=100,
        current_index=60,
    )
    assert no_crossing.available is True
    assert no_crossing.triggers == ()

    all_triggered = find_trigger_boundaries(
        state_evaluator(lambda _price: True),
        minimum=20,
        maximum=100,
        current_index=60,
    )
    assert all_triggered.available is True
    assert all_triggered.triggers == ()
    current = next(point for point in all_triggered.curve if point.index_price == 60)
    assert current.triggered is True


@pytest.mark.parametrize(
    ("predicate", "direction"),
    [
        (lambda price: price <= 60, TriggerDirection.LOWER),
        (lambda price: price >= 60, TriggerDirection.UPPER),
    ],
)
def test_root_search_preserves_a_boundary_at_the_current_index(predicate, direction):
    result = find_trigger_boundaries(
        state_evaluator(predicate),
        minimum=20,
        maximum=100,
        current_index=60,
    )

    assert result.available is True
    assert len(result.triggers) == 1
    assert result.triggers[0].price == 60
    assert result.triggers[0].direction is direction


def test_root_search_discards_partial_curve_when_any_point_is_unavailable():
    def evaluator(price: float):
        if abs(price - 60) <= 1e-9:
            return ScenarioPointCalculation(
                available=False,
                index_price=price,
                adjusted_equity=None,
                maintenance_margin=None,
                margin_ratio=None,
                derived_wallet_balance=None,
                eligible_long_option_value=None,
                settlement_cashflow=None,
                triggered=None,
                trigger_reason=None,
                issues=(),
            )
        return trigger_point(price, False)

    result = find_trigger_boundaries(
        evaluator,
        minimum=20,
        maximum=100,
        current_index=60,
    )
    assert result.available is False
    assert result.curve == ()
    assert result.triggers == ()


    def raising_evaluator(price: float):
        if abs(price - 60) <= 1e-9:
            raise ValueError("synthetic point failure")
        return trigger_point(price, False)

    raised = find_trigger_boundaries(
        raising_evaluator,
        minimum=20,
        maximum=100,
        current_index=60,
    )
    assert raised.available is False
    assert raised.curve == ()
    assert raised.issues[0].code is RiskIssueCode.INVALID_NUMERIC_VALUE


def test_root_search_fails_closed_when_64_refinements_cannot_reach_tolerance():
    result = find_trigger_boundaries(
        state_evaluator(lambda price: price >= 5e307),
        minimum=1,
        maximum=1e308,
        current_index=1e307,
    )

    assert result.available is False
    assert result.curve == ()
    assert result.triggers == ()
    assert dict(result.issues[0].context)["field"] == (
        "boundary_refinement_did_not_converge"
    )


def test_fixture_scenario_analysis_is_deterministic_and_includes_current_node():
    evaluation_input = classic_evaluation_input()
    first = analyze_scenario(evaluation_input)
    second = analyze_scenario(evaluation_input)

    assert first == second
    assert first.status is ScenarioStatus.OK
    assert len(first.curve) >= DEFAULT_SCAN_SAMPLE_COUNT
    assert first.current_point is not None
    assert first.current_point.index_price == pytest.approx(
        evaluation_input.market.index_price
    )
    assert len(first.current_point.contributions) == 4
    assert list(first.triggers) == sorted(first.triggers, key=lambda item: item.price)


def test_batch_scenarios_preserve_order_and_isolate_invalid_iv_shifts():
    evaluation_input = classic_evaluation_input()
    shifts = (-55, -10, 0, 20, 50)

    results = analyze_scenarios(evaluation_input, shifts)

    assert [result.iv_shift_vol_points for result in results] == list(shifts)
    assert results[0].status is ScenarioStatus.UNAVAILABLE
    assert RiskIssueCode.NON_POSITIVE_SCENARIO_VOLATILITY in {
        issue.code for issue in results[0].issues
    }
    assert all(result.status is ScenarioStatus.OK for result in results[1:])
    assert all(result.curve == () for result in results)

    for shift, batched in zip(shifts[1:], results[1:]):
        single = analyze_scenario(
            replace(evaluation_input, iv_shift_vol_points=shift)
        )
        assert batched.triggers == single.triggers
        assert batched.lower_trigger == single.lower_trigger
        assert batched.upper_trigger == single.upper_trigger
        assert batched.current_point is not None
        assert single.current_point is not None
        assert batched.current_point.margin_ratio == pytest.approx(
            single.current_point.margin_ratio
        )


def test_batch_summary_prepares_shared_inputs_once(monkeypatch):
    evaluation_input = classic_evaluation_input()
    calls = {"blocking": 0, "wallet": 0}
    original_blocking = risk_calculator._blocking_input_issues
    original_wallet = risk_calculator.derive_wallet_balance

    def counting_blocking(*args, **kwargs):
        calls["blocking"] += 1
        return original_blocking(*args, **kwargs)

    def counting_wallet(*args, **kwargs):
        calls["wallet"] += 1
        return original_wallet(*args, **kwargs)

    monkeypatch.setattr(risk_calculator, "_blocking_input_issues", counting_blocking)
    monkeypatch.setattr(risk_calculator, "derive_wallet_balance", counting_wallet)

    results = analyze_scenarios(evaluation_input, (-10, 0, 10, 20, 30, 40, 50))

    assert all(result.status is ScenarioStatus.OK for result in results)
    assert calls == {"blocking": 1, "wallet": 1}
    assert all(
        result.current_point is not None
        and result.current_point.contributions == ()
        for result in results
    )


def test_batch_detail_keeps_full_calculation_curve_and_current_contributions():
    evaluation_input = classic_evaluation_input()

    summary, detail = analyze_scenarios(
        evaluation_input,
        (0, 10),
        detail_iv_shift_vol_points=10,
    )

    assert summary.curve == ()
    assert summary.break_even_prices is None
    assert summary.remaining_position_count is None
    assert len(detail.curve) >= DEFAULT_SCAN_SAMPLE_COUNT
    assert detail.break_even_prices is not None
    assert detail.break_even_prices == tuple(sorted(detail.break_even_prices))
    assert detail.remaining_position_count == 6
    assert detail.current_point is not None
    assert len(detail.current_point.contributions) == 4
    assert all(point.contributions == () for point in detail.curve)


def test_all_expired_detail_skips_pnl_root_search_and_reports_no_remaining_positions(
    monkeypatch,
):
    evaluation_input = classic_evaluation_input(
        valuation_time_ms=max(contract.expiry_ms for contract in classic_evaluation_input().contracts)
    )

    def unexpected_root_search(*args, **kwargs):
        raise AssertionError("PNL root search must be skipped when no positions remain")

    monkeypatch.setattr(
        risk_calculator,
        "_fast_search_pnl_roots",
        unexpected_root_search,
    )

    detail = analyze_scenario(evaluation_input)

    assert detail.status is ScenarioStatus.OK
    assert detail.remaining_position_count == 0
    assert detail.break_even_prices == ()
    assert detail.current_point is not None
    assert detail.current_point.unrealized_pnl == 0
    contract_by_symbol = {
        contract.symbol: contract for contract in evaluation_input.contracts
    }
    expected_settlement = sum(
        settle_expired_position(
            contract_by_symbol[position.symbol],
            evaluation_input.market.index_price,
            position.quantity,
        ).settlement_cashflow
        for position in evaluation_input.account.positions
    )
    assert detail.current_point.settlement_cashflow == pytest.approx(
        expected_settlement
    )


def test_time_constants_remain_equal_to_frozen_contract():
    assert YEAR_MS == VECTORS["numericConvention"]["yearMilliseconds"]
    assert NEAR_EXPIRY_MS == 1_800_000
    assert DEFAULT_SCAN_SAMPLE_COUNT == VECTORS["rootSearch"]["minimumSampleCount"]
    assert DEFAULT_BOUNDARY_TOLERANCE == VECTORS["rootSearch"][
        "boundaryToleranceUsdt"
    ]
