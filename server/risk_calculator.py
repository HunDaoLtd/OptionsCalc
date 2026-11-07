from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

try:
    from .risk_types import (
        BlackScholesResult,
        CalculationIssue,
        CurrentAccountCalculation,
        IssueContextValue,
        MaintenanceMarginContributionCalculation,
        OptionSide,
        PositionSide,
        RiskContract,
        RiskEvaluationInput,
        RiskIssueCode,
        RiskPosition,
        RootSearchResult,
        ScenarioAnalysis,
        ScenarioPointCalculation,
        ScenarioStatus,
        SettlementResult,
        TriggerBoundaryCalculation,
        TriggerDirection,
        TriggerEvaluation,
        TriggerReason,
    )
except ImportError:
    from risk_types import (
        BlackScholesResult,
        CalculationIssue,
        CurrentAccountCalculation,
        IssueContextValue,
        MaintenanceMarginContributionCalculation,
        OptionSide,
        PositionSide,
        RiskContract,
        RiskEvaluationInput,
        RiskIssueCode,
        RiskPosition,
        RootSearchResult,
        ScenarioAnalysis,
        ScenarioPointCalculation,
        ScenarioStatus,
        SettlementResult,
        TriggerBoundaryCalculation,
        TriggerDirection,
        TriggerEvaluation,
        TriggerReason,
    )


YEAR_MS = 365 * 24 * 60 * 60 * 1000
NEAR_EXPIRY_MS = 30 * 60 * 1000
DEFAULT_SCAN_SAMPLE_COUNT = 2001
DEFAULT_BOUNDARY_TOLERANCE = 1.0
PNL_ZERO_TOLERANCE = 0.01
NODE_MERGE_TOLERANCE = 1e-9
MAX_BISECTION_ITERATIONS = 64
LIQUIDATION_MARGIN_RATIO = 0.95
EXERCISE_FEE_RATE = 0.00015
EXERCISE_FEE_CAP_RATE = 0.10
_SQRT_TWO = math.sqrt(2.0)


@dataclass(frozen=True, slots=True)
class _PreparedScenario:
    evaluation_input: RiskEvaluationInput
    positions: tuple[RiskPosition, ...]
    contract_by_symbol: dict[str, RiskContract]
    derived_wallet_balance: float
    warnings: tuple[CalculationIssue, ...]


@dataclass(frozen=True, slots=True)
class _PreparedBatchPosition:
    position: RiskPosition
    contract: RiskContract
    option_side: OptionSide
    quantity: float
    absolute_quantity: float
    current_mark_price: float
    strike_price: float
    unit: float
    maintenance_margin_rate: float
    min_maintenance_margin_rate: float
    liquidation_fee_rate: float
    expired: bool
    time_years: float | None
    sqrt_time: float | None
    discount: float | None
    risk_free_interest: float | None


@dataclass(frozen=True, slots=True)
class _PreparedBatchPricer:
    sigma_sqrt_time: float
    drift_time: float


@dataclass(frozen=True, slots=True)
class _PreparedBatchShift:
    iv_shift_vol_points: float
    pricers: tuple[_PreparedBatchPricer | None, ...]


@dataclass(frozen=True, slots=True)
class _PreparedBatchShiftRequest:
    iv_shift_vol_points: float | None
    prepared: _PreparedBatchShift | None
    issues: tuple[CalculationIssue, ...]


@dataclass(frozen=True, slots=True)
class _PreparedBatch:
    evaluation_input: RiskEvaluationInput
    positions: tuple[_PreparedBatchPosition, ...]
    derived_wallet_balance: float
    remaining_position_count: int
    remaining_pnl_baseline: float
    warnings: tuple[CalculationIssue, ...]


@dataclass(frozen=True, slots=True)
class _FastScenarioPoint:
    available: bool
    index_price: float
    adjusted_equity: float | None
    maintenance_margin: float | None
    margin_ratio: float | None
    eligible_long_option_value: float | None
    settlement_cashflow: float | None
    triggered: bool | None
    trigger_reason: TriggerReason | None
    unrealized_pnl: float | None = None
    issue: CalculationIssue | None = None


@dataclass(frozen=True, slots=True)
class _FastSearchResult:
    available: bool
    triggers: tuple[TriggerBoundaryCalculation, ...]
    issues: tuple[CalculationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _PnlRootSearchResult:
    available: bool
    prices: tuple[float, ...]


def _finite(value: float | int, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be a finite number")
    return parsed


def _positive(value: float | int, name: str) -> float:
    parsed = _finite(value, name)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _non_negative(value: float | int, name: str) -> float:
    parsed = _finite(value, name)
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _option_side(value: OptionSide | str) -> OptionSide:
    try:
        return OptionSide(value)
    except (TypeError, ValueError) as error:
        raise ValueError("option_side must be CALL or PUT") from error


def _position_side(value: PositionSide | str) -> PositionSide:
    try:
        return PositionSide(value)
    except (TypeError, ValueError) as error:
        raise ValueError("position_side must be LONG or SHORT") from error


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _issue(
    code: RiskIssueCode,
    **context: IssueContextValue,
) -> CalculationIssue:
    return CalculationIssue(code=code, context=tuple(sorted(context.items())))


def _issue_sort_key(issue: CalculationIssue):
    return (
        issue.code.value,
        tuple((key, type(value).__name__, repr(value)) for key, value in issue.context),
    )


def _sorted_unique_issues(
    issues: Iterable[CalculationIssue],
) -> tuple[CalculationIssue, ...]:
    return tuple(sorted(set(issues), key=_issue_sort_key))


def black_scholes_prices(
    index_price: float,
    strike_price: float,
    time_years: float,
    risk_free_interest: float,
    volatility: float,
    unit: float,
) -> BlackScholesResult:
    """Return unrounded Black-Scholes call and put prices for one contract."""

    spot = _positive(index_price, "index_price")
    strike = _positive(strike_price, "strike_price")
    time_value = _positive(time_years, "time_years")
    rate = _finite(risk_free_interest, "risk_free_interest")
    sigma = _positive(volatility, "volatility")
    contract_unit = _positive(unit, "unit")

    try:
        sqrt_time = math.sqrt(time_value)
        sigma_sqrt_time = sigma * sqrt_time
        d1 = (
            math.log(spot / strike)
            + (rate + sigma * sigma / 2.0) * time_value
        ) / sigma_sqrt_time
        d2 = d1 - sigma_sqrt_time
        discount = math.exp(-rate * time_value)
    except ArithmeticError as error:
        raise ValueError("Black-Scholes intermediate result is invalid") from error

    call_price = contract_unit * (
        spot * _normal_cdf(d1) - strike * discount * _normal_cdf(d2)
    )
    put_price = contract_unit * (
        strike * discount * _normal_cdf(-d2) - spot * _normal_cdf(-d1)
    )
    if not all(math.isfinite(value) for value in (d1, d2, call_price, put_price)):
        raise ValueError("Black-Scholes result must be finite")

    # Valid Black-Scholes prices are non-negative. Clamp only floating-point
    # cancellation residue at the mathematical lower bound.
    return BlackScholesResult(
        d1=d1,
        d2=d2,
        call_price=max(call_price, 0.0),
        put_price=max(put_price, 0.0),
    )


def option_otm_amount(
    option_side: OptionSide | str,
    index_price: float,
    strike_price: float,
) -> float:
    side = _option_side(option_side)
    spot = _positive(index_price, "index_price")
    strike = _positive(strike_price, "strike_price")
    return min(spot - strike, 0.0) if side is OptionSide.CALL else min(strike - spot, 0.0)


def position_maintenance_margin(
    contract: RiskContract,
    index_price: float,
    option_price: float,
    quantity: float,
) -> float:
    spot = _positive(index_price, "index_price")
    price = _non_negative(option_price, "option_price")
    signed_quantity = _finite(quantity, "quantity")
    if signed_quantity >= 0:
        return 0.0

    strike = _positive(contract.strike_price, "strike_price")
    if contract.unit is None:
        raise ValueError("unit is required")
    if contract.maintenance_margin_rate is None:
        raise ValueError("maintenance_margin_rate is required")
    if contract.min_maintenance_margin_rate is None:
        raise ValueError("min_maintenance_margin_rate is required")
    if contract.liquidation_fee_rate is None:
        raise ValueError("liquidation_fee_rate is required")

    unit = _positive(contract.unit, "unit")
    maintenance_rate = _non_negative(
        contract.maintenance_margin_rate, "maintenance_margin_rate"
    )
    minimum_rate = _non_negative(
        contract.min_maintenance_margin_rate, "min_maintenance_margin_rate"
    )
    liquidation_rate = _non_negative(
        contract.liquidation_fee_rate, "liquidation_fee_rate"
    )
    otm_amount = option_otm_amount(contract.option_side, spot, strike)
    per_contract = (
        max(spot * minimum_rate, spot * maintenance_rate + otm_amount) * unit
        + price
        + spot * unit * liquidation_rate
    )
    result = per_contract * abs(signed_quantity)
    if not math.isfinite(result) or result < 0:
        raise ValueError("maintenance margin result must be finite and non-negative")
    return result


def derive_wallet_balance(
    official_adjusted_equity: float,
    current_long_position_mark_values: Iterable[float],
) -> float:
    adjusted_equity = _finite(official_adjusted_equity, "official_adjusted_equity")
    long_value = sum(
        _non_negative(value, "current_long_position_mark_value")
        for value in current_long_position_mark_values
    )
    result = adjusted_equity - long_value
    if not math.isfinite(result):
        raise ValueError("derived wallet balance must be finite")
    return result


def calculate_scenario_adjusted_equity(
    derived_wallet_balance: float,
    scenario_long_option_values: Iterable[float],
    expired_position_settlement_cashflows: Iterable[float],
) -> float:
    wallet = _finite(derived_wallet_balance, "derived_wallet_balance")
    long_value = sum(
        _non_negative(value, "scenario_long_option_value")
        for value in scenario_long_option_values
    )
    settlement_cashflow = sum(
        _finite(value, "expired_position_settlement_cashflow")
        for value in expired_position_settlement_cashflows
    )
    result = wallet + long_value + settlement_cashflow
    if not math.isfinite(result):
        raise ValueError("scenario adjusted equity must be finite")
    return result


def settle_expired_position(
    contract: RiskContract,
    settlement_price: float,
    quantity: float,
) -> SettlementResult:
    spot = _positive(settlement_price, "settlement_price")
    strike = _positive(contract.strike_price, "strike_price")
    signed_quantity = _finite(quantity, "quantity")
    if contract.unit is None:
        raise ValueError("unit is required")
    unit = _positive(contract.unit, "unit")
    side = _option_side(contract.option_side)

    intrinsic = max(spot - strike, 0.0) if side is OptionSide.CALL else max(strike - spot, 0.0)
    payoff_per_contract = intrinsic * unit
    exercise_fee_per_contract = min(
        EXERCISE_FEE_RATE * spot * unit,
        EXERCISE_FEE_CAP_RATE * intrinsic * unit,
    )
    exercise_fee = exercise_fee_per_contract * abs(signed_quantity)
    settlement_cashflow = payoff_per_contract * signed_quantity - exercise_fee
    if not all(
        math.isfinite(value)
        for value in (payoff_per_contract, exercise_fee, settlement_cashflow)
    ):
        raise ValueError("settlement result must be finite")
    return SettlementResult(
        payoff_per_contract=payoff_per_contract,
        exercise_fee=exercise_fee,
        settlement_cashflow=settlement_cashflow,
    )


def evaluate_liquidation_trigger(
    maintenance_margin: float,
    adjusted_equity: float,
    derived_wallet_balance: float,
    eligible_long_option_value: float,
) -> TriggerEvaluation:
    margin = _non_negative(maintenance_margin, "maintenance_margin")
    equity = _finite(adjusted_equity, "adjusted_equity")
    wallet = _finite(derived_wallet_balance, "derived_wallet_balance")
    long_value = _non_negative(
        eligible_long_option_value, "eligible_long_option_value"
    )

    if margin > 0 and equity <= 0:
        return TriggerEvaluation(
            triggered=True,
            reason=TriggerReason.NON_POSITIVE_ADJUSTED_EQUITY,
            margin_ratio=None,
        )

    margin_ratio = margin / equity if equity > 0 else None
    if margin > 0 and margin_ratio is not None and margin_ratio >= LIQUIDATION_MARGIN_RATIO:
        return TriggerEvaluation(
            triggered=True,
            reason=TriggerReason.MARGIN_RATIO_THRESHOLD,
            margin_ratio=margin_ratio,
        )

    if margin == 0 and wallet < 0:
        if long_value <= 0:
            return TriggerEvaluation(
                triggered=None,
                reason=None,
                margin_ratio=margin_ratio,
                issue=_issue(RiskIssueCode.NO_ELIGIBLE_LONG_OPTION_VALUE),
            )
        if abs(wallet) / long_value >= LIQUIDATION_MARGIN_RATIO:
            return TriggerEvaluation(
                triggered=True,
                reason=TriggerReason.NEGATIVE_WALLET_LONG_VALUE_RATIO,
                margin_ratio=margin_ratio,
            )

    return TriggerEvaluation(
        triggered=False,
        reason=None,
        margin_ratio=margin_ratio,
    )


def _validate_position(position: RiskPosition) -> tuple[CalculationIssue, ...]:
    issues: list[CalculationIssue] = []
    quantity: float | None = None
    side: PositionSide | None = None
    try:
        quantity = _finite(position.quantity, "quantity")
    except ValueError:
        issues.append(
            _issue(
                RiskIssueCode.INVALID_NUMERIC_VALUE,
                symbol=position.symbol,
                field="quantity",
            )
        )

    try:
        side = _position_side(position.position_side)
    except ValueError:
        issues.append(
            _issue(
                RiskIssueCode.INVALID_NUMERIC_VALUE,
                symbol=position.symbol,
                field="position_side",
            )
        )

    if quantity is not None and side is not None:
        if quantity != 0 and (
            (side is PositionSide.LONG and quantity < 0)
            or (side is PositionSide.SHORT and quantity > 0)
        ):
            issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=position.symbol,
                    field="quantity_side",
                )
            )

    for field, value, validator in (
        ("mark_value", position.mark_value, _non_negative),
        ("unrealized_pnl", position.unrealized_pnl, _finite),
    ):
        try:
            validator(value, field)
        except ValueError:
            issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=position.symbol,
                    field=field,
                )
            )
    return _sorted_unique_issues(issues)


def _contract_map(
    contracts: tuple[RiskContract, ...],
) -> tuple[dict[str, RiskContract], tuple[CalculationIssue, ...]]:
    result: dict[str, RiskContract] = {}
    issues: list[CalculationIssue] = []
    for contract in contracts:
        if contract.symbol in result:
            issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=contract.symbol,
                    field="duplicate_contract",
                )
            )
            continue
        result[contract.symbol] = contract
    return result, _sorted_unique_issues(issues)


def _validate_contract(
    contract: RiskContract,
    *,
    require_scenario_mark: bool,
) -> tuple[CalculationIssue, ...]:
    issues: list[CalculationIssue] = []
    numeric_fields = (
        ("strike_price", contract.strike_price, True),
        ("unit", contract.unit, True),
        ("maintenance_margin_rate", contract.maintenance_margin_rate, False),
        ("min_maintenance_margin_rate", contract.min_maintenance_margin_rate, False),
        ("liquidation_fee_rate", contract.liquidation_fee_rate, False),
    )
    try:
        _option_side(contract.option_side)
    except ValueError:
        issues.append(
            _issue(
                RiskIssueCode.INVALID_NUMERIC_VALUE,
                symbol=contract.symbol,
                field="option_side",
            )
        )

    try:
        expiry_ms = _finite(contract.expiry_ms, "expiry_ms")
        if expiry_ms <= 0 or not float(expiry_ms).is_integer():
            raise ValueError
    except (TypeError, ValueError):
        issues.append(
            _issue(
                RiskIssueCode.INVALID_NUMERIC_VALUE,
                symbol=contract.symbol,
                field="expiry_ms",
            )
        )

    for field, value, positive in numeric_fields:
        if value is None:
            issues.append(
                _issue(
                    RiskIssueCode.MISSING_CONTRACT,
                    symbol=contract.symbol,
                    field=field,
                )
            )
            continue
        try:
            (_positive if positive else _non_negative)(value, field)
        except ValueError:
            issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=contract.symbol,
                    field=field,
                )
            )

    mark_fields: tuple[tuple[str, float | None], ...] = (("mark_price", contract.mark_price),)
    if require_scenario_mark:
        mark_fields += (
            ("mark_iv", contract.mark_iv),
            ("risk_free_interest", contract.risk_free_interest),
        )
    for field, value in mark_fields:
        if value is None:
            issues.append(
                _issue(RiskIssueCode.MISSING_MARK, symbol=contract.symbol, field=field)
            )
            continue
        try:
            if field in ("mark_price", "mark_iv"):
                _non_negative(value, field)
            else:
                _finite(value, field)
        except ValueError:
            issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=contract.symbol,
                    field=field,
                )
            )
    return _sorted_unique_issues(issues)


def _blocking_input_issues(
    evaluation_input: RiskEvaluationInput,
    *,
    require_scenario_mark: bool,
) -> tuple[dict[str, RiskContract], tuple[RiskPosition, ...], tuple[CalculationIssue, ...]]:
    issues: list[CalculationIssue] = []
    contract_by_symbol, contract_issues = _contract_map(evaluation_input.contracts)
    issues.extend(contract_issues)

    for field, value, validator in (
        ("market.index_price", evaluation_input.market.index_price, _positive),
        (
            "official_adjusted_equity",
            evaluation_input.account.official_adjusted_equity,
            _finite,
        ),
        (
            "official_maintenance_margin",
            evaluation_input.account.official_maintenance_margin,
            _non_negative,
        ),
    ):
        try:
            validator(value, field)
        except ValueError:
            issues.append(_issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=field))

    positions = tuple(
        sorted(
            (
                position
                for position in evaluation_input.account.positions
                if position.quantity != 0
            ),
            key=lambda position: position.symbol,
        )
    )
    validated_symbols: set[str] = set()
    for position in positions:
        issues.extend(_validate_position(position))
        contract = contract_by_symbol.get(position.symbol)
        if contract is None:
            issues.append(_issue(RiskIssueCode.MISSING_CONTRACT, symbol=position.symbol))
            continue
        if contract.symbol not in validated_symbols:
            issues.extend(
                _validate_contract(
                    contract, require_scenario_mark=require_scenario_mark
                )
            )
            validated_symbols.add(contract.symbol)

    return contract_by_symbol, positions, _sorted_unique_issues(issues)


def _contributions_with_shares(
    values: Iterable[tuple[RiskPosition, RiskContract, float, float]],
) -> tuple[MaintenanceMarginContributionCalculation, ...]:
    rows = list(values)
    total_margin = sum(margin for _, _, _, margin in rows)
    contributions = [
        MaintenanceMarginContributionCalculation(
            symbol=position.symbol,
            option_side=_option_side(contract.option_side),
            position_side=_position_side(position.position_side),
            quantity=float(position.quantity),
            entry_price=position.entry_price,
            scenario_option_price=option_price,
            maintenance_margin=margin,
            maintenance_margin_share=(margin / total_margin if total_margin > 0 else 0.0),
            expiry_ms=int(contract.expiry_ms),
        )
        for position, contract, option_price, margin in rows
    ]
    return tuple(
        sorted(
            contributions,
            key=lambda contribution: (
                -contribution.maintenance_margin,
                contribution.symbol,
            ),
        )
    )


def calculate_current_account(
    evaluation_input: RiskEvaluationInput,
) -> CurrentAccountCalculation:
    contract_by_symbol, positions, issues = _blocking_input_issues(
        evaluation_input, require_scenario_mark=False
    )
    if issues:
        return CurrentAccountCalculation(
            available=False,
            derived_wallet_balance=None,
            long_option_value=None,
            model_maintenance_margin=None,
            margin_ratio=None,
            trigger=None,
            issues=issues,
        )

    try:
        long_option_value = sum(
            _non_negative(position.mark_value, "mark_value")
            for position in positions
            if _position_side(position.position_side) is PositionSide.LONG
        )
        derived_wallet = derive_wallet_balance(
            evaluation_input.account.official_adjusted_equity,
            (position.mark_value for position in positions if position.quantity > 0),
        )
        raw_contributions: list[tuple[RiskPosition, RiskContract, float, float]] = []
        model_margin = 0.0
        for position in positions:
            if position.quantity >= 0:
                continue
            contract = contract_by_symbol[position.symbol]
            assert contract.mark_price is not None
            margin = position_maintenance_margin(
                contract,
                evaluation_input.market.index_price,
                contract.mark_price,
                position.quantity,
            )
            model_margin += margin
            raw_contributions.append((position, contract, contract.mark_price, margin))

        trigger = evaluate_liquidation_trigger(
            evaluation_input.account.official_maintenance_margin,
            evaluation_input.account.official_adjusted_equity,
            derived_wallet,
            long_option_value,
        )
        if trigger.issue is not None:
            return CurrentAccountCalculation(
                available=False,
                derived_wallet_balance=None,
                long_option_value=None,
                model_maintenance_margin=None,
                margin_ratio=None,
                trigger=None,
                issues=(trigger.issue,),
            )
        return CurrentAccountCalculation(
            available=True,
            derived_wallet_balance=derived_wallet,
            long_option_value=long_option_value,
            model_maintenance_margin=model_margin,
            margin_ratio=trigger.margin_ratio,
            trigger=trigger,
            contributions=_contributions_with_shares(raw_contributions),
        )
    except ValueError as error:
        return CurrentAccountCalculation(
            available=False,
            derived_wallet_balance=None,
            long_option_value=None,
            model_maintenance_margin=None,
            margin_ratio=None,
            trigger=None,
            issues=(
                _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=str(error)),
            ),
        )


def _prepare_scenario(
    evaluation_input: RiskEvaluationInput,
) -> tuple[_PreparedScenario | None, tuple[CalculationIssue, ...]]:
    contract_by_symbol, positions, blocking_issues = _blocking_input_issues(
        evaluation_input, require_scenario_mark=True
    )
    issues = list(blocking_issues)
    warnings: list[CalculationIssue] = []

    iv_shift: float | None = None
    valuation_time: float | None = None
    try:
        iv_shift = _finite(
            evaluation_input.iv_shift_vol_points, "iv_shift_vol_points"
        )
    except ValueError:
        issues.append(
            _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="iv_shift_vol_points")
        )
    try:
        valuation_time = _finite(
            evaluation_input.valuation_time_ms, "valuation_time_ms"
        )
        if valuation_time < 0 or not valuation_time.is_integer():
            raise ValueError
    except ValueError:
        issues.append(
            _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="valuation_time_ms")
        )

    if iv_shift is None or valuation_time is None:
        return None, _sorted_unique_issues(issues)

    for position in positions:
        contract = contract_by_symbol.get(position.symbol)
        if contract is None:
            continue
        try:
            expiry_ms = _positive(contract.expiry_ms, "expiry_ms")
            if not expiry_ms.is_integer():
                raise ValueError
        except ValueError:
            continue
        if expiry_ms - NEAR_EXPIRY_MS <= valuation_time < expiry_ms:
            issues.append(
                _issue(
                    RiskIssueCode.NEAR_EXPIRY_TRIGGER_DISABLED,
                    symbol=position.symbol,
                    expiryMs=int(expiry_ms),
                )
            )
        elif valuation_time >= expiry_ms:
            warnings.extend(
                (
                    _issue(
                        RiskIssueCode.EXPIRED_POSITION_SETTLED,
                        symbol=position.symbol,
                        expiryMs=int(expiry_ms),
                    ),
                    _issue(
                        RiskIssueCode.EXPIRY_SETTLEMENT_APPROXIMATION,
                        symbol=position.symbol,
                        expiryMs=int(expiry_ms),
                    ),
                )
            )

        if contract.mark_iv is not None:
            try:
                sigma = _finite(contract.mark_iv, "mark_iv") + iv_shift / 100.0
                if sigma <= 0:
                    issues.append(
                        _issue(
                            RiskIssueCode.NON_POSITIVE_SCENARIO_VOLATILITY,
                            symbol=position.symbol,
                            volatility=sigma,
                        )
                    )
            except ValueError:
                pass

    if issues:
        return None, _sorted_unique_issues((*issues, *warnings))

    try:
        derived_wallet = derive_wallet_balance(
            evaluation_input.account.official_adjusted_equity,
            (position.mark_value for position in positions if position.quantity > 0),
        )
    except ValueError as error:
        return None, (
            _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=str(error)),
        )

    return (
        _PreparedScenario(
            evaluation_input=evaluation_input,
            positions=positions,
            contract_by_symbol=contract_by_symbol,
            derived_wallet_balance=derived_wallet,
            warnings=_sorted_unique_issues(warnings),
        ),
        (),
    )


def _unavailable_point(
    index_price: float | None,
    issues: Iterable[CalculationIssue],
) -> ScenarioPointCalculation:
    return ScenarioPointCalculation(
        available=False,
        index_price=index_price,
        adjusted_equity=None,
        maintenance_margin=None,
        margin_ratio=None,
        derived_wallet_balance=None,
        eligible_long_option_value=None,
        settlement_cashflow=None,
        triggered=None,
        trigger_reason=None,
        unrealized_pnl=None,
        issues=_sorted_unique_issues(issues),
    )


def _evaluate_prepared_scenario_point(
    prepared: _PreparedScenario,
    index_price: float,
) -> ScenarioPointCalculation:
    try:
        spot = _positive(index_price, "index_price")
        valuation_time = float(prepared.evaluation_input.valuation_time_ms)
        iv_shift = float(prepared.evaluation_input.iv_shift_vol_points)
        long_option_values: list[float] = []
        settlement_cashflows: list[float] = []
        position_value_changes: list[float] = []
        expired_position_unrealized_pnls: list[float] = []
        remaining_position_count = 0
        raw_contributions: list[tuple[RiskPosition, RiskContract, float, float]] = []

        for position in prepared.positions:
            contract = prepared.contract_by_symbol[position.symbol]
            assert contract.mark_price is not None
            if valuation_time >= contract.expiry_ms:
                settlement = settle_expired_position(
                    contract, spot, position.quantity
                )
                settlement_cashflows.append(settlement.settlement_cashflow)
                expired_position_unrealized_pnls.append(position.unrealized_pnl)
                continue

            remaining_position_count += 1
            assert contract.mark_iv is not None
            assert contract.risk_free_interest is not None
            assert contract.unit is not None
            time_years = (contract.expiry_ms - valuation_time) / YEAR_MS
            volatility = contract.mark_iv + iv_shift / 100.0
            prices = black_scholes_prices(
                spot,
                contract.strike_price,
                time_years,
                contract.risk_free_interest,
                volatility,
                contract.unit,
            )
            option_price = (
                prices.call_price
                if _option_side(contract.option_side) is OptionSide.CALL
                else prices.put_price
            )
            position_value_changes.append(
                (option_price - contract.mark_price) * position.quantity
            )
            if position.quantity > 0:
                long_option_values.append(option_price * abs(position.quantity))
            else:
                margin = position_maintenance_margin(
                    contract, spot, option_price, position.quantity
                )
                raw_contributions.append((position, contract, option_price, margin))

        maintenance_margin = sum(
            margin for _, _, _, margin in raw_contributions
        )
        eligible_long_value = sum(long_option_values)
        settlement_cashflow = sum(settlement_cashflows)
        unrealized_pnl = 0.0 if remaining_position_count == 0 else (
            prepared.evaluation_input.account.unrealized_pnl
            - sum(expired_position_unrealized_pnls)
            + sum(position_value_changes)
        )
        adjusted_equity = calculate_scenario_adjusted_equity(
            prepared.derived_wallet_balance,
            long_option_values,
            settlement_cashflows,
        )
        trigger = evaluate_liquidation_trigger(
            maintenance_margin,
            adjusted_equity,
            prepared.derived_wallet_balance,
            eligible_long_value,
        )
        if trigger.issue is not None or trigger.triggered is None:
            return _unavailable_point(
                spot,
                (*prepared.warnings, *(filter(None, (trigger.issue,)))),
            )

        return ScenarioPointCalculation(
            available=True,
            index_price=spot,
            adjusted_equity=adjusted_equity,
            maintenance_margin=maintenance_margin,
            margin_ratio=trigger.margin_ratio,
            derived_wallet_balance=prepared.derived_wallet_balance,
            eligible_long_option_value=eligible_long_value,
            settlement_cashflow=settlement_cashflow,
            triggered=trigger.triggered,
            trigger_reason=trigger.reason,
            unrealized_pnl=unrealized_pnl,
            contributions=_contributions_with_shares(raw_contributions),
            issues=prepared.warnings,
        )
    except (AssertionError, ValueError) as error:
        return _unavailable_point(
            float(index_price) if isinstance(index_price, (int, float)) else None,
            (
                *prepared.warnings,
                _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=str(error)),
            ),
        )


def evaluate_scenario_point(
    evaluation_input: RiskEvaluationInput,
    index_price: float,
) -> ScenarioPointCalculation:
    prepared, issues = _prepare_scenario(evaluation_input)
    if prepared is None:
        try:
            spot: float | None = _positive(index_price, "index_price")
        except ValueError:
            spot = None
            issues = _sorted_unique_issues(
                (*issues, _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="index_price"))
            )
        return _unavailable_point(spot, issues)
    return _evaluate_prepared_scenario_point(prepared, index_price)


def _prepare_scenario_batch(
    evaluation_input: RiskEvaluationInput,
    iv_shifts_vol_points: Iterable[float],
) -> tuple[_PreparedBatch | None, tuple[_PreparedBatchShiftRequest, ...]]:
    parsed_shifts: list[tuple[float | None, tuple[CalculationIssue, ...]]] = []
    for raw_shift in iv_shifts_vol_points:
        try:
            parsed_shifts.append((_finite(raw_shift, "iv_shift_vol_points"), ()))
        except ValueError:
            parsed_shifts.append(
                (
                    None,
                    (_issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="iv_shift_vol_points"),),
                )
            )

    contract_by_symbol, positions, blocking_issues = _blocking_input_issues(
        evaluation_input, require_scenario_mark=True
    )
    issues = list(blocking_issues)
    warnings: list[CalculationIssue] = []
    try:
        valuation_time = _finite(
            evaluation_input.valuation_time_ms, "valuation_time_ms"
        )
        if valuation_time < 0 or not valuation_time.is_integer():
            raise ValueError
    except ValueError:
        valuation_time = None
        issues.append(
            _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="valuation_time_ms")
        )

    prepared_positions: list[_PreparedBatchPosition] = []
    if valuation_time is not None:
        for position in positions:
            contract = contract_by_symbol.get(position.symbol)
            if contract is None:
                continue
            required_values = (
                contract.unit,
                contract.maintenance_margin_rate,
                contract.min_maintenance_margin_rate,
                contract.liquidation_fee_rate,
                contract.mark_price,
                contract.risk_free_interest,
            )
            if any(value is None for value in required_values):
                continue
            try:
                expiry_ms = float(contract.expiry_ms)
            except (TypeError, ValueError):
                continue
            if expiry_ms - NEAR_EXPIRY_MS <= valuation_time < expiry_ms:
                issues.append(
                    _issue(
                        RiskIssueCode.NEAR_EXPIRY_TRIGGER_DISABLED,
                        symbol=position.symbol,
                        expiryMs=int(expiry_ms),
                    )
                )
            expired = valuation_time >= expiry_ms
            if expired:
                warnings.extend(
                    (
                        _issue(
                            RiskIssueCode.EXPIRED_POSITION_SETTLED,
                            symbol=position.symbol,
                            expiryMs=int(expiry_ms),
                        ),
                        _issue(
                            RiskIssueCode.EXPIRY_SETTLEMENT_APPROXIMATION,
                            symbol=position.symbol,
                            expiryMs=int(expiry_ms),
                        ),
                    )
                )

            assert contract.unit is not None
            assert contract.maintenance_margin_rate is not None
            assert contract.min_maintenance_margin_rate is not None
            assert contract.liquidation_fee_rate is not None
            assert contract.mark_price is not None
            assert contract.risk_free_interest is not None
            time_years: float | None = None
            sqrt_time: float | None = None
            discount: float | None = None
            if not expired:
                time_years = (expiry_ms - valuation_time) / YEAR_MS
                try:
                    sqrt_time = math.sqrt(time_years)
                    discount = math.exp(-contract.risk_free_interest * time_years)
                    if not all(
                        math.isfinite(value)
                        for value in (time_years, sqrt_time, discount)
                    ):
                        raise ArithmeticError
                except (ArithmeticError, OverflowError, ValueError):
                    issues.append(
                        _issue(
                            RiskIssueCode.INVALID_NUMERIC_VALUE,
                            symbol=position.symbol,
                            field="black_scholes_constants",
                        )
                    )
                    continue

            prepared_positions.append(
                _PreparedBatchPosition(
                    position=position,
                    contract=contract,
                    option_side=_option_side(contract.option_side),
                    quantity=float(position.quantity),
                    absolute_quantity=abs(float(position.quantity)),
                    current_mark_price=float(contract.mark_price),
                    strike_price=float(contract.strike_price),
                    unit=float(contract.unit),
                    maintenance_margin_rate=float(contract.maintenance_margin_rate),
                    min_maintenance_margin_rate=float(
                        contract.min_maintenance_margin_rate
                    ),
                    liquidation_fee_rate=float(contract.liquidation_fee_rate),
                    expired=expired,
                    time_years=time_years,
                    sqrt_time=sqrt_time,
                    discount=discount,
                    risk_free_interest=float(contract.risk_free_interest),
                )
            )

    try:
        derived_wallet = derive_wallet_balance(
            evaluation_input.account.official_adjusted_equity,
            (position.mark_value for position in positions if position.quantity > 0),
        )
    except ValueError as error:
        derived_wallet = None
        issues.append(_issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=str(error)))

    shared_issues = _sorted_unique_issues((*issues, *warnings))
    if issues or valuation_time is None or derived_wallet is None:
        return (
            None,
            tuple(
                _PreparedBatchShiftRequest(
                    iv_shift_vol_points=shift,
                    prepared=None,
                    issues=_sorted_unique_issues((*shared_issues, *shift_issues)),
                )
                for shift, shift_issues in parsed_shifts
            ),
        )

    batch = _PreparedBatch(
        evaluation_input=evaluation_input,
        positions=tuple(prepared_positions),
        derived_wallet_balance=derived_wallet,
        remaining_position_count=sum(
            1 for position in prepared_positions if not position.expired
        ),
        remaining_pnl_baseline=(
            evaluation_input.account.unrealized_pnl
            - sum(
                position.position.unrealized_pnl
                for position in prepared_positions
                if position.expired
            )
        ),
        warnings=_sorted_unique_issues(warnings),
    )
    shift_requests: list[_PreparedBatchShiftRequest] = []
    for shift, shift_issues in parsed_shifts:
        if shift is None:
            shift_requests.append(
                _PreparedBatchShiftRequest(
                    iv_shift_vol_points=None,
                    prepared=None,
                    issues=shift_issues,
                )
            )
            continue

        scenario_issues = list(shift_issues)
        pricers: list[_PreparedBatchPricer | None] = []
        for prepared_position in batch.positions:
            if prepared_position.expired:
                pricers.append(None)
                continue
            assert prepared_position.contract.mark_iv is not None
            assert prepared_position.time_years is not None
            assert prepared_position.sqrt_time is not None
            assert prepared_position.risk_free_interest is not None
            sigma = float(prepared_position.contract.mark_iv) + shift / 100.0
            if not math.isfinite(sigma) or sigma <= 0:
                scenario_issues.append(
                    _issue(
                        RiskIssueCode.NON_POSITIVE_SCENARIO_VOLATILITY,
                        symbol=prepared_position.position.symbol,
                        volatility=sigma,
                    )
                )
                pricers.append(None)
                continue
            sigma_sqrt_time = sigma * prepared_position.sqrt_time
            drift_time = (
                prepared_position.risk_free_interest + sigma * sigma / 2.0
            ) * prepared_position.time_years
            if not all(math.isfinite(value) for value in (sigma_sqrt_time, drift_time)):
                scenario_issues.append(
                    _issue(
                        RiskIssueCode.INVALID_NUMERIC_VALUE,
                        symbol=prepared_position.position.symbol,
                        field="black_scholes_constants",
                    )
                )
                pricers.append(None)
                continue
            pricers.append(
                _PreparedBatchPricer(
                    sigma_sqrt_time=sigma_sqrt_time,
                    drift_time=drift_time,
                )
            )

        if scenario_issues:
            shift_requests.append(
                _PreparedBatchShiftRequest(
                    iv_shift_vol_points=shift,
                    prepared=None,
                    issues=_sorted_unique_issues((*scenario_issues, *batch.warnings)),
                )
            )
        else:
            shift_requests.append(
                _PreparedBatchShiftRequest(
                    iv_shift_vol_points=shift,
                    prepared=_PreparedBatchShift(
                        iv_shift_vol_points=shift,
                        pricers=tuple(pricers),
                    ),
                    issues=(),
                )
            )
    return batch, tuple(shift_requests)


def _batch_scan_nodes(
    evaluation_input: RiskEvaluationInput,
    positions: tuple[_PreparedBatchPosition, ...],
    sample_count: int,
    boundary_tolerance: float,
) -> tuple[tuple[float, ...] | None, float | None, tuple[CalculationIssue, ...]]:
    try:
        lower_bound = _positive(evaluation_input.scan_minimum, "minimum")
        upper_bound = _positive(evaluation_input.scan_maximum, "maximum")
        current = _positive(evaluation_input.market.index_price, "current_index")
        tolerance = _positive(boundary_tolerance, "boundary_tolerance")
        if upper_bound <= lower_bound:
            raise ValueError("maximum must be greater than minimum")
        if current < lower_bound or current > upper_bound:
            raise ValueError("current_index must be inside the scan range")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            raise ValueError("sample_count must be an integer")
        if sample_count < DEFAULT_SCAN_SAMPLE_COUNT:
            raise ValueError("sample_count must be at least 2001")
    except ValueError as error:
        return None, None, (
            _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=str(error)),
        )

    step = (upper_bound - lower_bound) / (sample_count - 1)
    nodes = [lower_bound + step * index for index in range(sample_count)]
    nodes[-1] = upper_bound
    nodes.append(current)
    nodes.extend(
        position.strike_price
        for position in positions
        if lower_bound <= position.strike_price <= upper_bound
    )
    return _coalesced_nodes(nodes), tolerance, ()


def _fast_unavailable_point(
    spot: float,
    issue: CalculationIssue,
) -> _FastScenarioPoint:
    return _FastScenarioPoint(
        available=False,
        index_price=spot,
        adjusted_equity=None,
        maintenance_margin=None,
        margin_ratio=None,
        eligible_long_option_value=None,
        settlement_cashflow=None,
        triggered=None,
        trigger_reason=None,
        issue=issue,
    )


def _finish_fast_point(
    batch: _PreparedBatch,
    spot: float,
    maintenance_margin: float,
    eligible_long_value: float,
    settlement_cashflow: float,
    unrealized_pnl: float,
) -> _FastScenarioPoint:
    adjusted_equity = (
        batch.derived_wallet_balance + eligible_long_value + settlement_cashflow
    )
    if not all(
        math.isfinite(value)
        for value in (
            maintenance_margin,
            eligible_long_value,
            settlement_cashflow,
            unrealized_pnl,
            adjusted_equity,
        )
    ) or maintenance_margin < 0 or eligible_long_value < 0:
        return _fast_unavailable_point(
            spot,
            _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="scenario_point"),
        )

    if maintenance_margin > 0 and adjusted_equity <= 0:
        return _FastScenarioPoint(
            available=True,
            index_price=spot,
            adjusted_equity=adjusted_equity,
            maintenance_margin=maintenance_margin,
            margin_ratio=None,
            eligible_long_option_value=eligible_long_value,
            settlement_cashflow=settlement_cashflow,
            triggered=True,
            trigger_reason=TriggerReason.NON_POSITIVE_ADJUSTED_EQUITY,
            unrealized_pnl=unrealized_pnl,
        )

    margin_ratio = (
        maintenance_margin / adjusted_equity if adjusted_equity > 0 else None
    )
    if (
        maintenance_margin > 0
        and margin_ratio is not None
        and margin_ratio >= LIQUIDATION_MARGIN_RATIO
    ):
        return _FastScenarioPoint(
            available=True,
            index_price=spot,
            adjusted_equity=adjusted_equity,
            maintenance_margin=maintenance_margin,
            margin_ratio=margin_ratio,
            eligible_long_option_value=eligible_long_value,
            settlement_cashflow=settlement_cashflow,
            triggered=True,
            trigger_reason=TriggerReason.MARGIN_RATIO_THRESHOLD,
            unrealized_pnl=unrealized_pnl,
        )

    if maintenance_margin == 0 and batch.derived_wallet_balance < 0:
        if eligible_long_value <= 0:
            return _fast_unavailable_point(
                spot,
                _issue(RiskIssueCode.NO_ELIGIBLE_LONG_OPTION_VALUE),
            )
        if (
            abs(batch.derived_wallet_balance) / eligible_long_value
            >= LIQUIDATION_MARGIN_RATIO
        ):
            return _FastScenarioPoint(
                available=True,
                index_price=spot,
                adjusted_equity=adjusted_equity,
                maintenance_margin=maintenance_margin,
                margin_ratio=margin_ratio,
                eligible_long_option_value=eligible_long_value,
                settlement_cashflow=settlement_cashflow,
                triggered=True,
                trigger_reason=TriggerReason.NEGATIVE_WALLET_LONG_VALUE_RATIO,
                unrealized_pnl=unrealized_pnl,
            )

    return _FastScenarioPoint(
        available=True,
        index_price=spot,
        adjusted_equity=adjusted_equity,
        maintenance_margin=maintenance_margin,
        margin_ratio=margin_ratio,
        eligible_long_option_value=eligible_long_value,
        settlement_cashflow=settlement_cashflow,
        triggered=False,
        trigger_reason=None,
        unrealized_pnl=unrealized_pnl,
    )


def _evaluate_batch_node(
    batch: _PreparedBatch,
    shifts: tuple[_PreparedBatchShift, ...],
    spot: float,
) -> tuple[_FastScenarioPoint, ...]:
    scenario_count = len(shifts)
    maintenance_margins = [0.0] * scenario_count
    long_values = [0.0] * scenario_count
    position_value_changes = [0.0] * scenario_count
    scenario_issues: list[CalculationIssue | None] = [None] * scenario_count
    settlement_cashflow = 0.0

    try:
        for position_index, prepared_position in enumerate(batch.positions):
            if prepared_position.expired:
                intrinsic = (
                    max(spot - prepared_position.strike_price, 0.0)
                    if prepared_position.option_side is OptionSide.CALL
                    else max(prepared_position.strike_price - spot, 0.0)
                )
                payoff = intrinsic * prepared_position.unit
                exercise_fee = min(
                    EXERCISE_FEE_RATE * spot * prepared_position.unit,
                    EXERCISE_FEE_CAP_RATE * intrinsic * prepared_position.unit,
                ) * prepared_position.absolute_quantity
                position_settlement_cashflow = (
                    payoff * prepared_position.quantity - exercise_fee
                )
                settlement_cashflow += position_settlement_cashflow
                continue

            assert prepared_position.discount is not None
            log_moneyness = math.log(spot / prepared_position.strike_price)
            if prepared_position.option_side is OptionSide.CALL:
                otm_amount = min(spot - prepared_position.strike_price, 0.0)
            else:
                otm_amount = min(prepared_position.strike_price - spot, 0.0)
            base_margin = (
                max(
                    spot * prepared_position.min_maintenance_margin_rate,
                    spot * prepared_position.maintenance_margin_rate + otm_amount,
                )
                * prepared_position.unit
            )
            liquidation_reserve = (
                spot
                * prepared_position.unit
                * prepared_position.liquidation_fee_rate
            )

            for scenario_index, shift in enumerate(shifts):
                if scenario_issues[scenario_index] is not None:
                    continue
                pricer = shift.pricers[position_index]
                assert pricer is not None
                d1 = (log_moneyness + pricer.drift_time) / pricer.sigma_sqrt_time
                d2 = d1 - pricer.sigma_sqrt_time
                if prepared_position.option_side is OptionSide.CALL:
                    option_price = prepared_position.unit * (
                        spot * (0.5 * (1.0 + math.erf(d1 / _SQRT_TWO)))
                        - prepared_position.strike_price
                        * prepared_position.discount
                        * (0.5 * (1.0 + math.erf(d2 / _SQRT_TWO)))
                    )
                else:
                    option_price = prepared_position.unit * (
                        prepared_position.strike_price
                        * prepared_position.discount
                        * (0.5 * (1.0 + math.erf(-d2 / _SQRT_TWO)))
                        - spot * (0.5 * (1.0 + math.erf(-d1 / _SQRT_TWO)))
                    )
                if not math.isfinite(option_price):
                    scenario_issues[scenario_index] = _issue(
                        RiskIssueCode.INVALID_NUMERIC_VALUE,
                        field="scenario_point",
                    )
                    continue
                option_price = max(option_price, 0.0)
                position_value_changes[scenario_index] += (
                    option_price - prepared_position.current_mark_price
                ) * prepared_position.quantity
                if prepared_position.quantity > 0:
                    long_values[scenario_index] += (
                        option_price * prepared_position.absolute_quantity
                    )
                else:
                    maintenance_margins[scenario_index] += (
                        base_margin + option_price + liquidation_reserve
                    ) * prepared_position.absolute_quantity
    except (ArithmeticError, AssertionError, ValueError):
        issue = _issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="scenario_point")
        return tuple(_fast_unavailable_point(spot, issue) for _ in shifts)

    return tuple(
        _fast_unavailable_point(spot, scenario_issues[index])
        if scenario_issues[index] is not None
        else _finish_fast_point(
            batch,
            spot,
            maintenance_margins[index],
            long_values[index],
            settlement_cashflow,
            0.0
            if batch.remaining_position_count == 0
            else batch.remaining_pnl_baseline + position_value_changes[index],
        )
        for index in range(scenario_count)
    )


def _fast_to_scenario_point(
    batch: _PreparedBatch,
    point: _FastScenarioPoint,
) -> ScenarioPointCalculation:
    if not point.available:
        return _unavailable_point(
            point.index_price,
            (*batch.warnings, *(filter(None, (point.issue,)))),
        )
    return ScenarioPointCalculation(
        available=True,
        index_price=point.index_price,
        adjusted_equity=point.adjusted_equity,
        maintenance_margin=point.maintenance_margin,
        margin_ratio=point.margin_ratio,
        derived_wallet_balance=batch.derived_wallet_balance,
        eligible_long_option_value=point.eligible_long_option_value,
        settlement_cashflow=point.settlement_cashflow,
        triggered=point.triggered,
        trigger_reason=point.trigger_reason,
        unrealized_pnl=point.unrealized_pnl,
        issues=batch.warnings,
    )


def _fast_search_boundaries(
    curve: tuple[_FastScenarioPoint, ...],
    evaluator: Callable[[float], _FastScenarioPoint],
    *,
    lower_bound: float,
    upper_bound: float,
    current: float,
    tolerance: float,
) -> _FastSearchResult:
    unavailable = tuple(point for point in curve if not point.available)
    if unavailable:
        return _FastSearchResult(
            available=False,
            triggers=(),
            issues=_sorted_unique_issues(
                point.issue for point in unavailable if point.issue is not None
            ),
        )

    boundaries: list[TriggerBoundaryCalculation] = []
    collected_issues: list[CalculationIssue] = []
    for left_point, right_point in zip(curve, curve[1:]):
        if left_point.triggered == right_point.triggered:
            continue
        left_price = left_point.index_price
        right_price = right_point.index_price
        left_state = bool(left_point.triggered)
        touches_current = (
            abs(left_price - current) <= NODE_MERGE_TOLERANCE
            or abs(right_price - current) <= NODE_MERGE_TOLERANCE
        )
        refined_left = left_point
        refined_right = right_point

        for _ in range(MAX_BISECTION_ITERATIONS):
            if right_price - left_price <= tolerance:
                break
            midpoint = (left_price + right_price) / 2.0
            midpoint_result = evaluator(midpoint)
            if not midpoint_result.available:
                if midpoint_result.issue is not None:
                    collected_issues.append(midpoint_result.issue)
                return _FastSearchResult(
                    available=False,
                    triggers=(),
                    issues=_sorted_unique_issues(collected_issues),
                )
            if bool(midpoint_result.triggered) == left_state:
                left_price = midpoint
                refined_left = midpoint_result
            else:
                right_price = midpoint
                refined_right = midpoint_result

        if right_price - left_price > tolerance:
            collected_issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    field="boundary_refinement_did_not_converge",
                )
            )
            return _FastSearchResult(
                available=False,
                triggers=(),
                issues=_sorted_unique_issues(collected_issues),
            )

        boundary_price = (left_price + right_price) / 2.0
        triggered_point = refined_left if left_state else refined_right
        current_direction: TriggerDirection | None = None
        if touches_current:
            probe_distance = max(NODE_MERGE_TOLERANCE * 2.0, abs(current) * 1e-12)
            probe_points = (
                evaluator(max(lower_bound, current - probe_distance)),
                evaluator(min(upper_bound, current + probe_distance)),
            )
            if any(not point.available for point in probe_points):
                collected_issues.extend(
                    point.issue
                    for point in probe_points
                    if point.issue is not None
                )
                return _FastSearchResult(
                    available=False,
                    triggers=(),
                    issues=_sorted_unique_issues(collected_issues),
                )
            current_point = min(
                (left_point, right_point),
                key=lambda point: abs(point.index_price - current),
            )
            if any(
                point.triggered != current_point.triggered
                for point in probe_points
            ):
                boundary_price = current
                if probe_points[0].triggered:
                    current_direction = TriggerDirection.LOWER
                    triggered_point = probe_points[0]
                elif probe_points[1].triggered:
                    current_direction = TriggerDirection.UPPER
                    triggered_point = probe_points[1]

        if triggered_point.trigger_reason is None:
            return _FastSearchResult(
                available=False,
                triggers=(),
                issues=(
                    _issue(
                        RiskIssueCode.INVALID_NUMERIC_VALUE,
                        field="triggered boundary must include a reason",
                    ),
                ),
            )
        if current_direction is not None:
            direction = current_direction
        elif boundary_price < current:
            direction = TriggerDirection.LOWER
        elif boundary_price > current:
            direction = TriggerDirection.UPPER
        else:
            direction = (
                TriggerDirection.LOWER
                if triggered_point.index_price <= boundary_price
                else TriggerDirection.UPPER
            )
        boundaries.append(
            TriggerBoundaryCalculation(
                price=boundary_price,
                direction=direction,
                reason=triggered_point.trigger_reason,
            )
        )

    deduplicated: list[TriggerBoundaryCalculation] = []
    for boundary in sorted(boundaries, key=lambda item: item.price):
        if (
            deduplicated
            and abs(boundary.price - deduplicated[-1].price) <= tolerance
        ):
            continue
        deduplicated.append(boundary)
    return _FastSearchResult(
        available=True,
        triggers=tuple(deduplicated),
        issues=_sorted_unique_issues(collected_issues),
    )


def _fast_search_pnl_roots(
    curve: tuple[_FastScenarioPoint, ...],
    evaluator: Callable[[float], _FastScenarioPoint],
    *,
    tolerance: float,
    pnl_tolerance: float = PNL_ZERO_TOLERANCE,
) -> _PnlRootSearchResult:
    """Find every modeled account-PNL zero represented by the scan curve."""

    def point_pnl(point: _FastScenarioPoint) -> float | None:
        value = point.unrealized_pnl
        if not point.available or value is None or not math.isfinite(value):
            return None
        return float(value)

    values: list[tuple[float, float]] = []
    for point in curve:
        value = point_pnl(point)
        if value is None:
            return _PnlRootSearchResult(available=False, prices=())
        values.append((point.index_price, value))
    if not values:
        return _PnlRootSearchResult(available=True, prices=())

    roots: list[float] = []
    run_start: float | None = None
    run_end: float | None = None
    for price, value in values:
        if abs(value) <= pnl_tolerance:
            if run_start is None:
                run_start = price
            run_end = price
        elif run_start is not None and run_end is not None:
            roots.append((run_start + run_end) / 2.0)
            run_start = None
            run_end = None
    if run_start is not None and run_end is not None:
        roots.append((run_start + run_end) / 2.0)

    for (left_price, left_value), (right_price, right_value) in zip(
        values, values[1:]
    ):
        if (
            abs(left_value) <= pnl_tolerance
            or abs(right_value) <= pnl_tolerance
            or left_value * right_value >= 0
        ):
            continue
        left_sign = left_value < 0
        exact_root: float | None = None
        for _ in range(MAX_BISECTION_ITERATIONS):
            if right_price - left_price <= tolerance:
                break
            midpoint = (left_price + right_price) / 2.0
            midpoint_value = point_pnl(evaluator(midpoint))
            if midpoint_value is None:
                return _PnlRootSearchResult(available=False, prices=())
            if abs(midpoint_value) <= pnl_tolerance:
                exact_root = midpoint
                break
            if (midpoint_value < 0) == left_sign:
                left_price = midpoint
            else:
                right_price = midpoint
        roots.append(
            exact_root
            if exact_root is not None
            else (left_price + right_price) / 2.0
        )

    golden_ratio = (math.sqrt(5.0) - 1.0) / 2.0
    minimum_interval = min(tolerance / 1024.0, 0.001)
    for left, middle, right in zip(values, values[1:], values[2:]):
        left_price, left_value = left
        middle_price, middle_value = middle
        right_price, right_value = right
        if (
            abs(middle_value) <= pnl_tolerance
            or left_value * right_value <= 0
            or abs(middle_value) >= abs(left_value)
            or abs(middle_value) >= abs(right_value)
        ):
            continue

        interval_left = left_price
        interval_right = right_price
        probe_left = interval_right - golden_ratio * (
            interval_right - interval_left
        )
        probe_right = interval_left + golden_ratio * (
            interval_right - interval_left
        )
        probe_left_value = point_pnl(evaluator(probe_left))
        probe_right_value = point_pnl(evaluator(probe_right))
        if probe_left_value is None or probe_right_value is None:
            return _PnlRootSearchResult(available=False, prices=())
        for _ in range(MAX_BISECTION_ITERATIONS):
            if interval_right - interval_left <= minimum_interval:
                break
            if abs(probe_left_value) <= abs(probe_right_value):
                interval_right = probe_right
                probe_right = probe_left
                probe_right_value = probe_left_value
                probe_left = interval_right - golden_ratio * (
                    interval_right - interval_left
                )
                probe_left_value = point_pnl(evaluator(probe_left))
                if probe_left_value is None:
                    return _PnlRootSearchResult(available=False, prices=())
            else:
                interval_left = probe_left
                probe_left = probe_right
                probe_left_value = probe_right_value
                probe_right = interval_left + golden_ratio * (
                    interval_right - interval_left
                )
                probe_right_value = point_pnl(evaluator(probe_right))
                if probe_right_value is None:
                    return _PnlRootSearchResult(available=False, prices=())

        candidates = (
            (middle_price, middle_value),
            (probe_left, probe_left_value),
            (probe_right, probe_right_value),
        )
        minimum_price, minimum_value = min(
            candidates, key=lambda item: abs(item[1])
        )
        if abs(minimum_value) <= pnl_tolerance:
            roots.append(minimum_price)

    deduplicated: list[float] = []
    for price in sorted(roots):
        if deduplicated and abs(price - deduplicated[-1]) <= tolerance:
            deduplicated[-1] = (deduplicated[-1] + price) / 2.0
        else:
            deduplicated.append(price)
    return _PnlRootSearchResult(
        available=True,
        prices=tuple(deduplicated),
    )


def _coalesced_nodes(values: Iterable[float]) -> tuple[float, ...]:
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > NODE_MERGE_TOLERANCE:
            result.append(value)
    return tuple(result)


def _root_search_error(field: str) -> RootSearchResult:
    return RootSearchResult(
        available=False,
        curve=(),
        triggers=(),
        issues=(_issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field=field),),
    )


def find_trigger_boundaries(
    evaluator: Callable[[float], ScenarioPointCalculation],
    *,
    minimum: float,
    maximum: float,
    current_index: float,
    extra_nodes: Iterable[float] = (),
    sample_count: int = DEFAULT_SCAN_SAMPLE_COUNT,
    boundary_tolerance: float = DEFAULT_BOUNDARY_TOLERANCE,
) -> RootSearchResult:
    try:
        lower_bound = _positive(minimum, "minimum")
        upper_bound = _positive(maximum, "maximum")
        current = _positive(current_index, "current_index")
        tolerance = _positive(boundary_tolerance, "boundary_tolerance")
        if upper_bound <= lower_bound:
            raise ValueError("maximum must be greater than minimum")
        if current < lower_bound or current > upper_bound:
            raise ValueError("current_index must be inside the scan range")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            raise ValueError("sample_count must be an integer")
        if sample_count < DEFAULT_SCAN_SAMPLE_COUNT:
            raise ValueError("sample_count must be at least 2001")
    except ValueError as error:
        return _root_search_error(str(error))

    def safe_evaluate(price: float) -> ScenarioPointCalculation:
        try:
            return evaluator(price)
        except ValueError:
            return _unavailable_point(
                price,
                (
                    _issue(
                        RiskIssueCode.INVALID_NUMERIC_VALUE,
                        field="scenario_point",
                    ),
                ),
            )

    step = (upper_bound - lower_bound) / (sample_count - 1)
    nodes = [lower_bound + step * index for index in range(sample_count)]
    nodes[-1] = upper_bound
    nodes.append(current)
    try:
        for node in extra_nodes:
            parsed = _finite(node, "extra_node")
            if lower_bound <= parsed <= upper_bound:
                nodes.append(parsed)
    except ValueError as error:
        return _root_search_error(str(error))
    ordered_nodes = _coalesced_nodes(nodes)

    curve = tuple(safe_evaluate(node) for node in ordered_nodes)
    unavailable_points = tuple(
        point
        for point in curve
        if not point.available or point.triggered is None
    )
    if unavailable_points:
        return RootSearchResult(
            available=False,
            curve=(),
            triggers=(),
            issues=_sorted_unique_issues(
                issue
                for point in unavailable_points
                for issue in point.issues
            ),
        )

    boundaries: list[TriggerBoundaryCalculation] = []
    collected_issues: list[CalculationIssue] = [
        issue for point in curve for issue in point.issues
    ]
    for left_point, right_point in zip(curve, curve[1:]):
        if left_point.triggered == right_point.triggered:
            continue
        assert left_point.index_price is not None
        assert right_point.index_price is not None
        left_price = left_point.index_price
        right_price = right_point.index_price
        left_state = bool(left_point.triggered)
        right_state = bool(right_point.triggered)
        touches_current = (
            abs(left_price - current) <= NODE_MERGE_TOLERANCE
            or abs(right_price - current) <= NODE_MERGE_TOLERANCE
        )
        refined_left = left_point
        refined_right = right_point

        for _ in range(MAX_BISECTION_ITERATIONS):
            if right_price - left_price <= tolerance:
                break
            midpoint = (left_price + right_price) / 2.0
            midpoint_result = safe_evaluate(midpoint)
            collected_issues.extend(midpoint_result.issues)
            if not midpoint_result.available or midpoint_result.triggered is None:
                return RootSearchResult(
                    available=False,
                    curve=(),
                    triggers=(),
                    issues=_sorted_unique_issues(collected_issues),
                )
            if bool(midpoint_result.triggered) == left_state:
                left_price = midpoint
                refined_left = midpoint_result
            else:
                right_price = midpoint
                refined_right = midpoint_result

        if right_price - left_price > tolerance:
            collected_issues.append(
                _issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    field="boundary_refinement_did_not_converge",
                )
            )
            return RootSearchResult(
                available=False,
                curve=(),
                triggers=(),
                issues=_sorted_unique_issues(collected_issues),
            )

        boundary_price = (left_price + right_price) / 2.0
        triggered_point = refined_left if left_state else refined_right
        current_direction: TriggerDirection | None = None
        if touches_current:
            probe_distance = max(NODE_MERGE_TOLERANCE * 2.0, abs(current) * 1e-12)
            probe_prices = (
                max(lower_bound, current - probe_distance),
                min(upper_bound, current + probe_distance),
            )
            probe_points = tuple(safe_evaluate(price) for price in probe_prices)
            collected_issues.extend(
                issue for point in probe_points for issue in point.issues
            )
            if any(
                not point.available or point.triggered is None
                for point in probe_points
            ):
                return RootSearchResult(
                    available=False,
                    curve=(),
                    triggers=(),
                    issues=_sorted_unique_issues(collected_issues),
                )
            current_point = min(
                (left_point, right_point),
                key=lambda point: abs(float(point.index_price) - current),
            )
            if any(
                point.triggered != current_point.triggered
                for point in probe_points
            ):
                boundary_price = current
                if probe_points[0].triggered:
                    current_direction = TriggerDirection.LOWER
                    triggered_point = probe_points[0]
                elif probe_points[1].triggered:
                    current_direction = TriggerDirection.UPPER
                    triggered_point = probe_points[1]

        if triggered_point.trigger_reason is None:
            return _root_search_error("triggered boundary must include a reason")
        if current_direction is not None:
            direction = current_direction
        elif boundary_price < current:
            direction = TriggerDirection.LOWER
        elif boundary_price > current:
            direction = TriggerDirection.UPPER
        else:
            direction = (
                TriggerDirection.LOWER
                if triggered_point.index_price is not None
                and triggered_point.index_price <= boundary_price
                else TriggerDirection.UPPER
            )
        boundaries.append(
            TriggerBoundaryCalculation(
                price=boundary_price,
                direction=direction,
                reason=triggered_point.trigger_reason,
            )
        )

    deduplicated: list[TriggerBoundaryCalculation] = []
    for boundary in sorted(boundaries, key=lambda item: item.price):
        if (
            deduplicated
            and abs(boundary.price - deduplicated[-1].price) <= tolerance
        ):
            continue
        deduplicated.append(boundary)

    return RootSearchResult(
        available=True,
        curve=curve,
        triggers=tuple(deduplicated),
        issues=_sorted_unique_issues(collected_issues),
    )


def _unavailable_scenario_analysis(
    iv_shift_vol_points: float | None,
    issues: Iterable[CalculationIssue],
) -> ScenarioAnalysis:
    return ScenarioAnalysis(
        status=ScenarioStatus.UNAVAILABLE,
        iv_shift_vol_points=iv_shift_vol_points,
        current_point=None,
        curve=(),
        triggers=(),
        lower_trigger=None,
        upper_trigger=None,
        break_even_prices=None,
        remaining_position_count=None,
        issues=_sorted_unique_issues(issues),
    )


def _scenario_current_point_with_contributions(
    batch: _PreparedBatch,
    iv_shift_vol_points: float,
) -> ScenarioPointCalculation:
    base = batch.evaluation_input
    detail_input = RiskEvaluationInput(
        market=base.market,
        account=base.account,
        contracts=base.contracts,
        valuation_time_ms=base.valuation_time_ms,
        iv_shift_vol_points=iv_shift_vol_points,
        scan_minimum=base.scan_minimum,
        scan_maximum=base.scan_maximum,
    )
    prepared, issues = _prepare_scenario(detail_input)
    if prepared is None:
        return _unavailable_point(base.market.index_price, issues)
    return _evaluate_prepared_scenario_point(prepared, base.market.index_price)


def analyze_scenarios(
    evaluation_input: RiskEvaluationInput,
    iv_shifts_vol_points: Iterable[float],
    *,
    detail_iv_shift_vol_points: float | None = None,
    sample_count: int = DEFAULT_SCAN_SAMPLE_COUNT,
    boundary_tolerance: float = DEFAULT_BOUNDARY_TOLERANCE,
) -> tuple[ScenarioAnalysis, ...]:
    """Analyze many IV shifts with one validation and shared price scan."""

    shifts = tuple(iv_shifts_vol_points)
    batch, requests = _prepare_scenario_batch(evaluation_input, shifts)
    if not requests:
        return ()
    if batch is None:
        return tuple(
            _unavailable_scenario_analysis(
                request.iv_shift_vol_points,
                request.issues,
            )
            for request in requests
        )

    nodes, tolerance, scan_issues = _batch_scan_nodes(
        evaluation_input,
        batch.positions,
        sample_count,
        boundary_tolerance,
    )
    if nodes is None or tolerance is None:
        return tuple(
            _unavailable_scenario_analysis(
                request.iv_shift_vol_points,
                (*request.issues, *scan_issues),
            )
            for request in requests
        )

    results: list[ScenarioAnalysis | None] = [None] * len(requests)
    valid_requests: list[tuple[int, _PreparedBatchShift]] = []
    for index, request in enumerate(requests):
        if request.prepared is None:
            results[index] = _unavailable_scenario_analysis(
                request.iv_shift_vol_points,
                request.issues,
            )
        else:
            valid_requests.append((index, request.prepared))

    if valid_requests:
        valid_shifts = tuple(prepared for _, prepared in valid_requests)
        curves: list[list[_FastScenarioPoint]] = [
            [] for _ in valid_requests
        ]
        for node in nodes:
            points = _evaluate_batch_node(batch, valid_shifts, node)
            for curve, point in zip(curves, points):
                curve.append(point)

        current = float(evaluation_input.market.index_price)
        lower_bound = float(evaluation_input.scan_minimum)
        upper_bound = float(evaluation_input.scan_maximum)
        for (result_index, prepared_shift), raw_curve in zip(
            valid_requests, curves
        ):
            curve = tuple(raw_curve)
            evaluator = lambda price, shift=prepared_shift: _evaluate_batch_node(
                batch, (shift,), price
            )[0]
            search = _fast_search_boundaries(
                curve,
                evaluator,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                current=current,
                tolerance=tolerance,
            )
            if not search.available:
                results[result_index] = _unavailable_scenario_analysis(
                    prepared_shift.iv_shift_vol_points,
                    (*batch.warnings, *search.issues),
                )
                continue

            current_fast = next(
                (
                    point
                    for point in curve
                    if abs(point.index_price - current) <= NODE_MERGE_TOLERANCE
                ),
                None,
            )
            if current_fast is None:
                results[result_index] = _unavailable_scenario_analysis(
                    prepared_shift.iv_shift_vol_points,
                    (
                        *batch.warnings,
                        _issue(
                            RiskIssueCode.INVALID_NUMERIC_VALUE,
                            field="current_index_node",
                        ),
                    ),
                )
                continue

            include_detail = (
                detail_iv_shift_vol_points is not None
                and prepared_shift.iv_shift_vol_points
                == detail_iv_shift_vol_points
            )
            break_even_prices: tuple[float, ...] | None = None
            if include_detail:
                if batch.remaining_position_count == 0:
                    break_even_prices = ()
                else:
                    pnl_search = _fast_search_pnl_roots(
                        curve,
                        evaluator,
                        tolerance=tolerance,
                    )
                    if pnl_search.available:
                        break_even_prices = pnl_search.prices
            current_point = (
                _scenario_current_point_with_contributions(
                    batch, prepared_shift.iv_shift_vol_points
                )
                if include_detail
                else _fast_to_scenario_point(batch, current_fast)
            )
            detailed_curve = (
                tuple(_fast_to_scenario_point(batch, point) for point in curve)
                if include_detail
                else ()
            )
            lower_prices = [
                boundary.price
                for boundary in search.triggers
                if boundary.price < current
            ]
            upper_prices = [
                boundary.price
                for boundary in search.triggers
                if boundary.price > current
            ]
            results[result_index] = ScenarioAnalysis(
                status=ScenarioStatus.OK,
                iv_shift_vol_points=prepared_shift.iv_shift_vol_points,
                current_point=current_point,
                curve=detailed_curve,
                triggers=search.triggers,
                lower_trigger=max(lower_prices) if lower_prices else None,
                upper_trigger=min(upper_prices) if upper_prices else None,
                break_even_prices=break_even_prices,
                remaining_position_count=(
                    batch.remaining_position_count if include_detail else None
                ),
                issues=_sorted_unique_issues((*batch.warnings, *search.issues)),
            )

    assert all(result is not None for result in results)
    return tuple(result for result in results if result is not None)


def analyze_scenario(
    evaluation_input: RiskEvaluationInput,
    *,
    sample_count: int = DEFAULT_SCAN_SAMPLE_COUNT,
    boundary_tolerance: float = DEFAULT_BOUNDARY_TOLERANCE,
) -> ScenarioAnalysis:
    try:
        detail_shift: float | None = _finite(
            evaluation_input.iv_shift_vol_points, "iv_shift_vol_points"
        )
    except ValueError:
        detail_shift = None
    return analyze_scenarios(
        evaluation_input,
        (evaluation_input.iv_shift_vol_points,),
        detail_iv_shift_vol_points=detail_shift,
        sample_count=sample_count,
        boundary_tolerance=boundary_tolerance,
    )[0]
