from __future__ import annotations

import math
import os
import time
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

try:
    from .risk_accounts import RiskAccountConfig, RiskAccountRegistry
    from .risk_binance import (
        BinancePrivateError,
        PrivateAccountCache,
        PrivateAccountSnapshot,
        RiskPublicCache,
        RiskPublicSnapshot,
    )
    from .risk_calculator import (
        DEFAULT_BOUNDARY_TOLERANCE,
        DEFAULT_SCAN_SAMPLE_COUNT,
        analyze_scenarios,
        derive_wallet_balance,
        evaluate_liquidation_trigger,
        position_maintenance_margin,
    )
    from .risk_types import (
        AccountConfigStatus,
        AccountEvaluationStatus,
        CalculationIssue,
        PositionSide,
        ReconciliationStatus,
        RiskAccountSnapshot,
        RiskContract,
        RiskEvaluationInput,
        RiskIssueCode,
        RiskPosition,
        ScenarioStatus,
    )
except ImportError:
    from risk_accounts import RiskAccountConfig, RiskAccountRegistry
    from risk_binance import (
        BinancePrivateError,
        PrivateAccountCache,
        PrivateAccountSnapshot,
        RiskPublicCache,
        RiskPublicSnapshot,
    )
    from risk_calculator import (
        DEFAULT_BOUNDARY_TOLERANCE,
        DEFAULT_SCAN_SAMPLE_COUNT,
        analyze_scenarios,
        derive_wallet_balance,
        evaluate_liquidation_trigger,
        position_maintenance_margin,
    )
    from risk_types import (
        AccountConfigStatus,
        AccountEvaluationStatus,
        CalculationIssue,
        PositionSide,
        ReconciliationStatus,
        RiskAccountSnapshot,
        RiskContract,
        RiskEvaluationInput,
        RiskIssueCode,
        RiskPosition,
        ScenarioStatus,
    )


MATCHED_DRIFT_PERCENT = 0.5
WARNING_DRIFT_PERCENT = 2.0
MAX_IV_SHIFTS = 21
DETAIL_CURVE_TARGET_POINTS = 401


@dataclass(frozen=True, slots=True)
class ValidatedEvaluateRequest:
    account_ids: tuple[str, ...]
    valuation_time_ms: int
    iv_shifts_vol_points: tuple[float, ...]
    detail_account_id: str | None
    detail_iv_shift_vol_points: float | None
    force_refresh: bool


@dataclass(frozen=True, slots=True)
class NormalizedPrivateAccount:
    snapshot: RiskAccountSnapshot | None
    all_positions: tuple[RiskPosition, ...]
    total_positions: int
    source_skew_ms: int
    issues: tuple[CalculationIssue, ...]
    unsupported: bool


class RiskRequestError(ValueError):
    def __init__(self, issues: tuple[CalculationIssue, ...]) -> None:
        super().__init__("invalid liquidation risk request")
        self.issues = issues


class RiskServiceError(RuntimeError):
    def __init__(self, issues: tuple[CalculationIssue, ...]) -> None:
        super().__init__("liquidation risk service unavailable")
        self.issues = issues


def calculation_issue(
    code: RiskIssueCode,
    **context: str | int | float | bool | None,
) -> CalculationIssue:
    return CalculationIssue(code=code, context=tuple(sorted(context.items())))


def sorted_issues(
    issues: tuple[CalculationIssue, ...] | list[CalculationIssue],
) -> tuple[CalculationIssue, ...]:
    return tuple(
        sorted(
            set(issues),
            key=lambda issue: (issue.code.value, repr(issue.context)),
        )
    )


def issue_to_wire(issue: CalculationIssue) -> dict[str, Any]:
    result: dict[str, Any] = {"code": issue.code.value}
    if issue.context:
        result["context"] = dict(issue.context)
    return result


def _reference_iv(
    account: RiskAccountSnapshot,
    contracts: tuple[RiskContract, ...],
    valuation_time_ms: int,
) -> float | None:
    """Return the absolute-position-weighted mark IV for unexpired positions."""
    contract_by_symbol = {contract.symbol: contract for contract in contracts}
    weighted_total = 0.0
    quantity_total = 0.0
    for position in account.positions:
        contract = contract_by_symbol.get(position.symbol)
        if (
            contract is None
            or contract.mark_iv is None
            or contract.expiry_ms <= valuation_time_ms
        ):
            continue
        quantity = abs(position.quantity)
        if quantity == 0:
            continue
        weighted_total += contract.mark_iv * quantity
        quantity_total += quantity
    return weighted_total / quantity_total if quantity_total > 0 else None


def error_response(
    code: str,
    issues: tuple[CalculationIssue, ...],
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "issues": [issue_to_wire(issue) for issue in issues],
        }
    }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer_ms(value: Any) -> int | None:
    parsed = _finite(value)
    if parsed is None or parsed < 0 or not parsed.is_integer():
        return None
    return int(parsed)


def validate_evaluate_request(
    payload: Any,
    registry: RiskAccountRegistry,
) -> ValidatedEvaluateRequest:
    if not isinstance(payload, dict):
        raise RiskRequestError((calculation_issue(RiskIssueCode.INVALID_JSON),))

    issues: list[CalculationIssue] = []
    raw_account_ids = payload.get("accountIds")
    account_ids: tuple[str, ...] = ()
    if (
        not isinstance(raw_account_ids, list)
        or not raw_account_ids
        or any(not isinstance(item, str) or not item for item in raw_account_ids)
        or len(set(raw_account_ids)) != len(raw_account_ids)
    ):
        issues.append(calculation_issue(RiskIssueCode.INVALID_ACCOUNT_IDS))
    else:
        account_ids = tuple(raw_account_ids)
        known_ids = {account.account_id for account in registry.accounts}
        for account_id in account_ids:
            if account_id not in known_ids:
                issues.append(
                    calculation_issue(
                        RiskIssueCode.UNKNOWN_ACCOUNT_ID,
                        accountId=account_id,
                    )
                )

    valuation_time = _integer_ms(payload.get("valuationTimeMs"))
    if valuation_time is None:
        issues.append(calculation_issue(RiskIssueCode.INVALID_VALUATION_TIME))

    raw_iv_shifts = payload.get("ivShiftsVolPoints")
    iv_shifts: tuple[float, ...] = ()
    if not isinstance(raw_iv_shifts, list) or not 1 <= len(raw_iv_shifts) <= MAX_IV_SHIFTS:
        issues.append(calculation_issue(RiskIssueCode.INVALID_IV_SHIFTS))
    else:
        parsed_shifts = tuple(_finite(value) for value in raw_iv_shifts)
        if (
            any(value is None for value in parsed_shifts)
            or len(set(parsed_shifts)) != len(parsed_shifts)
        ):
            issues.append(calculation_issue(RiskIssueCode.INVALID_IV_SHIFTS))
        else:
            iv_shifts = tuple(float(value) for value in parsed_shifts if value is not None)

    has_detail_account = "detailAccountId" in payload
    has_detail_iv = "detailIvShiftVolPoints" in payload
    detail_account_id: str | None = None
    detail_iv_shift: float | None = None
    if has_detail_account != has_detail_iv:
        issues.extend(
            (
                calculation_issue(RiskIssueCode.INVALID_DETAIL_ACCOUNT_ID),
                calculation_issue(RiskIssueCode.INVALID_DETAIL_IV_SHIFT),
            )
        )
    elif has_detail_account:
        raw_detail_account = payload.get("detailAccountId")
        if not isinstance(raw_detail_account, str) or raw_detail_account not in account_ids:
            issues.append(
                calculation_issue(RiskIssueCode.INVALID_DETAIL_ACCOUNT_ID)
            )
        else:
            detail_account_id = raw_detail_account
        parsed_detail_iv = _finite(payload.get("detailIvShiftVolPoints"))
        if parsed_detail_iv is None or parsed_detail_iv not in iv_shifts:
            issues.append(calculation_issue(RiskIssueCode.INVALID_DETAIL_IV_SHIFT))
        else:
            detail_iv_shift = parsed_detail_iv

    force_refresh = payload.get("forceRefresh", False)
    if not isinstance(force_refresh, bool):
        issues.append(calculation_issue(RiskIssueCode.INVALID_FORCE_REFRESH))

    if issues:
        raise RiskRequestError(sorted_issues(issues))
    assert valuation_time is not None
    return ValidatedEvaluateRequest(
        account_ids=account_ids,
        valuation_time_ms=valuation_time,
        iv_shifts_vol_points=iv_shifts,
        detail_account_id=detail_account_id,
        detail_iv_shift_vol_points=detail_iv_shift,
        force_refresh=force_refresh,
    )


def normalize_private_account(
    config: RiskAccountConfig,
    raw: PrivateAccountSnapshot,
) -> NormalizedPrivateAccount:
    issues: list[CalculationIssue] = []
    margin_mode = raw.margin_account.get("marginMode") or raw.margin_account.get(
        "accountType"
    )
    if raw.margin_account.get("portfolioMargin") is True or (
        margin_mode is not None
        and str(margin_mode).upper() not in {"CLASSIC", "REGULAR", "STANDARD"}
    ):
        issues.append(calculation_issue(RiskIssueCode.UNSUPPORTED_MARGIN_MODE))

    asset_rows = raw.margin_account.get("asset")
    usdt_asset = None
    if isinstance(asset_rows, list):
        usdt_asset = next(
            (
                item
                for item in asset_rows
                if isinstance(item, dict) and item.get("asset") == "USDT"
            ),
            None,
        )
    if usdt_asset is None:
        issues.append(calculation_issue(RiskIssueCode.UNSUPPORTED_QUOTE_ASSET))

    adjusted_equity = _finite(
        usdt_asset.get("adjustedEquity") if usdt_asset else None
    )
    maintenance_margin = _finite(
        usdt_asset.get("maintMargin") if usdt_asset else None
    )
    unrealized_pnl = _finite(
        usdt_asset.get("unrealizedPNL") if usdt_asset else None
    )
    if adjusted_equity is None or maintenance_margin is None or maintenance_margin < 0:
        issues.append(
            calculation_issue(RiskIssueCode.INVALID_NUMERIC_VALUE, field="account")
        )
    if unrealized_pnl is None:
        issues.append(
            calculation_issue(
                RiskIssueCode.INVALID_NUMERIC_VALUE,
                field="unrealized_pnl",
            )
        )

    positions: list[RiskPosition] = []
    btc_positions: list[RiskPosition] = []
    source_times: list[int] = []
    account_time = _integer_ms(raw.margin_account.get("time")) or raw.fetched_at_ms
    source_times.append(account_time)
    total_positions = 0
    for item in raw.positions:
        quantity = _finite(item.get("quantity"))
        if quantity is None:
            issues.append(
                calculation_issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    field="quantity",
                )
            )
            continue
        if quantity == 0:
            continue
        total_positions += 1
        symbol = str(item.get("symbol", ""))
        quote_asset = item.get("quoteAsset", "USDT")
        if quote_asset != "USDT":
            issues.append(
                calculation_issue(
                    RiskIssueCode.UNSUPPORTED_QUOTE_ASSET,
                    symbol=symbol,
                )
            )
        if not symbol.startswith("BTC-"):
            issues.append(
                calculation_issue(
                    RiskIssueCode.NON_BTC_OPEN_POSITION,
                    symbol=symbol,
                )
            )
        mark_value = _finite(item.get("markValue"))
        entry_price = _finite(item.get("entryPrice"))
        position_pnl = _finite(item.get("unrealizedPNL"))
        source_time = _integer_ms(item.get("time")) or account_time
        side = PositionSide.LONG if quantity > 0 else PositionSide.SHORT
        raw_side = item.get("side")
        if raw_side in ("LONG", "SHORT") and raw_side != side.value:
            issues.append(
                calculation_issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=symbol,
                    field="quantity_side",
                )
            )
        if (
            mark_value is None
            or (quantity > 0 and mark_value < 0)
            or position_pnl is None
        ):
            issues.append(
                calculation_issue(
                    RiskIssueCode.INVALID_NUMERIC_VALUE,
                    symbol=symbol,
                    field="position_value",
                )
            )
            continue
        # Binance currently returns signed markValue for some Short positions,
        # while its public example shows a positive market-value magnitude.
        # Position direction is already frozen in signed quantity, so keep the
        # normalized value non-negative to avoid encoding the side twice.
        normalized_mark_value = abs(mark_value)
        position = RiskPosition(
            symbol=symbol,
            position_side=side,
            quantity=quantity,
            entry_price=entry_price,
            mark_value=normalized_mark_value,
            unrealized_pnl=position_pnl,
            source_time_ms=source_time,
        )
        positions.append(position)
        source_times.append(source_time)
        if symbol.startswith("BTC-") and quote_asset == "USDT":
            btc_positions.append(position)

    unsupported_codes = {
        RiskIssueCode.UNSUPPORTED_MARGIN_MODE,
        RiskIssueCode.UNSUPPORTED_QUOTE_ASSET,
        RiskIssueCode.NON_BTC_OPEN_POSITION,
    }
    unsupported = any(issue.code in unsupported_codes for issue in issues)
    blocking_numeric = any(
        issue.code is RiskIssueCode.INVALID_NUMERIC_VALUE for issue in issues
    )
    snapshot = None
    if (
        not blocking_numeric
        and adjusted_equity is not None
        and maintenance_margin is not None
        and unrealized_pnl is not None
    ):
        snapshot = RiskAccountSnapshot(
            account_id=config.account_id,
            label=config.label,
            official_adjusted_equity=adjusted_equity,
            official_maintenance_margin=maintenance_margin,
            unrealized_pnl=unrealized_pnl,
            positions=tuple(btc_positions),
            source_time_ms=min(source_times),
            stale=raw.stale,
        )
    return NormalizedPrivateAccount(
        snapshot=snapshot,
        all_positions=tuple(positions),
        total_positions=total_positions,
        source_skew_ms=max(source_times) - min(source_times),
        issues=sorted_issues(issues),
        unsupported=unsupported,
    )


def _current_metrics(
    normalized: NormalizedPrivateAccount,
) -> dict[str, Any] | None:
    account = normalized.snapshot
    if account is None:
        return None
    if any(
        issue.code
        in {
            RiskIssueCode.UNSUPPORTED_MARGIN_MODE,
            RiskIssueCode.UNSUPPORTED_QUOTE_ASSET,
        }
        for issue in normalized.issues
    ):
        return None
    try:
        long_value = sum(
            position.mark_value
            for position in normalized.all_positions
            if position.quantity > 0
        )
        derived_wallet = derive_wallet_balance(
            account.official_adjusted_equity,
            (
                position.mark_value
                for position in normalized.all_positions
                if position.quantity > 0
            ),
        )
        trigger = evaluate_liquidation_trigger(
            account.official_maintenance_margin,
            account.official_adjusted_equity,
            derived_wallet,
            long_value,
        )
        if trigger.triggered is None or trigger.issue is not None:
            return None
        return {
            "adjustedEquity": account.official_adjusted_equity,
            "maintenanceMargin": account.official_maintenance_margin,
            "marginRatio": trigger.margin_ratio,
            "derivedWalletBalance": derived_wallet,
            "longOptionValue": long_value,
            "unrealizedPnl": account.unrealized_pnl,
            "sourceTime": account.source_time_ms,
            "stale": account.stale,
            "triggeredNow": bool(trigger.triggered),
            "triggerReason": trigger.reason.value if trigger.reason else None,
        }
    except ValueError:
        return None


def _contract_complete(contract: RiskContract | None) -> bool:
    if contract is None:
        return False
    values = (
        contract.unit,
        contract.maintenance_margin_rate,
        contract.min_maintenance_margin_rate,
        contract.liquidation_fee_rate,
        contract.mark_price,
        contract.mark_iv,
        contract.risk_free_interest,
    )
    return all(value is not None and math.isfinite(value) for value in values)


def _reconcile(
    normalized: NormalizedPrivateAccount,
    public: RiskPublicSnapshot,
) -> tuple[dict[str, Any], ReconciliationStatus, tuple[CalculationIssue, ...]]:
    account = normalized.snapshot
    contract_by_symbol = {contract.symbol: contract for contract in public.contracts}
    covered = sum(
        1
        for position in account.positions if account is not None
        and _contract_complete(contract_by_symbol.get(position.symbol))
    ) if account is not None else 0
    response: dict[str, Any] = {
        "status": ReconciliationStatus.UNRECONCILED.value,
        "mmDriftPercent": None,
        "coveredPositions": covered,
        "totalPositions": normalized.total_positions,
        "sourceSkewMs": normalized.source_skew_ms,
    }
    if normalized.unsupported:
        response["status"] = ReconciliationStatus.UNSUPPORTED.value
        return response, ReconciliationStatus.UNSUPPORTED, normalized.issues
    if account is None or covered != normalized.total_positions:
        issues = list(normalized.issues)
        if covered != normalized.total_positions:
            issues.append(calculation_issue(RiskIssueCode.MISSING_CONTRACT))
        return response, ReconciliationStatus.UNRECONCILED, sorted_issues(issues)

    try:
        model_margin = 0.0
        liquidation_reserve = 0.0
        for position in account.positions:
            if position.quantity >= 0:
                continue
            contract = contract_by_symbol[position.symbol]
            assert contract.mark_price is not None
            model_margin += position_maintenance_margin(
                contract,
                public.market.index_price,
                contract.mark_price,
                position.quantity,
            )
            assert contract.unit is not None
            assert contract.liquidation_fee_rate is not None
            liquidation_reserve += (
                public.market.index_price
                * contract.unit
                * contract.liquidation_fee_rate
                * abs(position.quantity)
            )
    except (AssertionError, KeyError, ValueError):
        return (
            response,
            ReconciliationStatus.UNRECONCILED,
            (calculation_issue(RiskIssueCode.MISSING_CONTRACT),),
        )
    denominator = max(abs(account.official_maintenance_margin), 1.0)
    inclusive_drift = (
        abs(model_margin - account.official_maintenance_margin) / denominator * 100
    )
    api_base_margin = model_margin - liquidation_reserve
    exclusive_drift = (
        abs(api_base_margin - account.official_maintenance_margin)
        / denominator
        * 100
    )
    # The Clearing Procedures include the liquidation-fee reserve in MM, while
    # the live marginAccount maintMargin field can report the same base MM
    # without that reserve. Scenario calculations stay inclusive; only the
    # reconciliation trust gate accepts these two observed account conventions.
    drift = min(inclusive_drift, exclusive_drift)
    response["mmDriftPercent"] = drift
    if drift <= MATCHED_DRIFT_PERCENT:
        status = ReconciliationStatus.MATCHED
        issues: tuple[CalculationIssue, ...] = ()
    elif drift <= WARNING_DRIFT_PERCENT:
        status = ReconciliationStatus.WARNING
        issues = (calculation_issue(RiskIssueCode.RECONCILIATION_DRIFT_WARNING),)
    else:
        status = ReconciliationStatus.UNRECONCILED
        issues = (
            calculation_issue(RiskIssueCode.UNRECONCILED_MAINTENANCE_MARGIN),
        )
    response["status"] = status.value
    return response, status, issues


def _boundary_to_wire(boundary) -> dict[str, Any]:
    return {
        "price": boundary.price,
        "direction": boundary.direction.value,
        "reason": boundary.reason.value,
    }


def _scenario_to_wire(analysis) -> dict[str, Any]:
    return {
        "ivShiftVolPoints": analysis.iv_shift_vol_points,
        "status": analysis.status.value,
        "lowerTrigger": analysis.lower_trigger,
        "upperTrigger": analysis.upper_trigger,
        "triggers": [_boundary_to_wire(item) for item in analysis.triggers],
        "warnings": [issue_to_wire(issue) for issue in analysis.issues],
    }


def _downsample_detail_curve(
    curve,
    *,
    mandatory_prices: tuple[float, ...],
    target_points: int = DETAIL_CURVE_TARGET_POINTS,
):
    if len(curve) <= target_points:
        return curve

    prices = [float(point.index_price) for point in curve]
    mandatory_indices = {0, len(curve) - 1}
    for price in mandatory_prices:
        insertion = bisect_left(prices, price)
        for index in (insertion - 1, insertion):
            if 0 <= index < len(curve):
                mandatory_indices.add(index)

    for index, (left, right) in enumerate(zip(curve, curve[1:]), start=1):
        if (
            left.triggered != right.triggered
            or left.trigger_reason != right.trigger_reason
            or (left.margin_ratio is None) != (right.margin_ratio is None)
        ):
            mandatory_indices.update((index - 1, index))

    remaining_budget = max(0, target_points - len(mandatory_indices))
    candidates = [
        index for index in range(len(curve)) if index not in mandatory_indices
    ]
    sampled_indices: set[int] = set()
    if remaining_budget >= len(candidates):
        sampled_indices.update(candidates)
    elif remaining_budget == 1:
        sampled_indices.add(candidates[len(candidates) // 2])
    elif remaining_budget > 1:
        for slot in range(remaining_budget):
            candidate_index = round(
                slot * (len(candidates) - 1) / (remaining_budget - 1)
            )
            sampled_indices.add(candidates[candidate_index])

    selected = sorted((*mandatory_indices, *sampled_indices))
    return tuple(curve[index] for index in selected)


def _detail_to_wire(
    analysis,
    *,
    mandatory_prices: tuple[float, ...] = (),
) -> dict[str, Any] | None:
    if (
        analysis.status is not ScenarioStatus.OK
        or analysis.current_point is None
        or analysis.remaining_position_count is None
    ):
        return None
    current = analysis.current_point
    curve = _downsample_detail_curve(
        analysis.curve,
        mandatory_prices=(
            *mandatory_prices,
            current.index_price,
            *(trigger.price for trigger in analysis.triggers),
        ),
    )
    return {
        "ivShiftVolPoints": analysis.iv_shift_vol_points,
        "breakEvenPrices": (
            list(analysis.break_even_prices)
            if analysis.break_even_prices is not None
            else None
        ),
        "remainingPositionCount": analysis.remaining_position_count,
        "curve": [
            {
                "indexPrice": point.index_price,
                "adjustedEquity": point.adjusted_equity,
                "maintenanceMargin": point.maintenance_margin,
                "marginRatio": point.margin_ratio,
                "triggered": point.triggered,
                "triggerReason": (
                    point.trigger_reason.value if point.trigger_reason else None
                ),
            }
            for point in curve
        ],
        "maintenanceMarginContributions": [
            {
                "symbol": row.symbol,
                "optionSide": row.option_side.value,
                "positionSide": row.position_side.value,
                "quantity": row.quantity,
                "entryPrice": row.entry_price,
                "scenarioOptionPrice": row.scenario_option_price,
                "maintenanceMargin": row.maintenance_margin,
                "maintenanceMarginShare": row.maintenance_margin_share,
                "expiryMs": row.expiry_ms,
            }
            for row in current.contributions
        ],
        "triggers": [_boundary_to_wire(item) for item in analysis.triggers],
        "warnings": [issue_to_wire(issue) for issue in analysis.issues],
    }


class LiquidationRiskService:
    def __init__(
        self,
        registry: RiskAccountRegistry,
        public_cache: RiskPublicCache,
        private_cache: PrivateAccountCache,
        *,
        now_seconds=time.time,
    ) -> None:
        self.registry = registry
        self.public_cache = public_cache
        self.private_cache = private_cache
        self._now_seconds = now_seconds

    def accounts_response(self) -> dict[str, Any]:
        if self.registry.default_account_id is None or self.registry.issues:
            raise RiskServiceError(
                self.registry.issues
                or (calculation_issue(RiskIssueCode.CONFIGURATION_ERROR),)
            )
        return {
            "defaultAccountId": self.registry.default_account_id,
            "accounts": [
                {
                    "id": account.account_id,
                    "label": account.label,
                    "status": account.status.value,
                }
                for account in self.registry.accounts
            ],
        }

    def _failure_account(
        self,
        config: RiskAccountConfig,
        issue: CalculationIssue,
    ) -> dict[str, Any]:
        return {
            "id": config.account_id,
            "label": config.label,
            "status": AccountEvaluationStatus.UNAVAILABLE.value,
            "referenceIv": None,
            "error": issue_to_wire(issue),
            "current": None,
            "reconciliation": None,
            "scenarios": None,
            "detail": None,
            "warnings": [],
        }

    def _evaluate_account(
        self,
        config: RiskAccountConfig,
        request: ValidatedEvaluateRequest,
        public: RiskPublicSnapshot,
        now_ms: int,
    ) -> tuple[dict[str, Any], bool]:
        try:
            raw = self.private_cache.fetch(
                config,
                force_refresh=request.force_refresh,
                now_ms=now_ms,
            )
        except BinancePrivateError as error:
            issue = calculation_issue(
                error.issue_code,
                accountId=config.account_id,
                endpoint=error.endpoint,
            )
            return self._failure_account(config, issue), False

        normalized = normalize_private_account(config, raw)
        current = _current_metrics(normalized)
        if normalized.snapshot is None:
            issue = next(
                iter(normalized.issues),
                calculation_issue(RiskIssueCode.INVALID_NUMERIC_VALUE),
            )
            return self._failure_account(config, issue), True

        reference_iv = _reference_iv(
            normalized.snapshot,
            public.contracts,
            request.valuation_time_ms,
        )

        reconciliation, reconciliation_status, reconcile_issues = _reconcile(
            normalized, public
        )
        account_warnings = list(reconcile_issues)
        if raw.stale:
            account_warnings.append(
                calculation_issue(
                    RiskIssueCode.ACCOUNT_DATA_STALE,
                    sourceTime=raw.fetched_at_ms,
                )
            )
        account_warnings = list(sorted_issues(account_warnings))

        if reconciliation_status is ReconciliationStatus.UNSUPPORTED:
            error = next(
                (
                    issue
                    for issue in normalized.issues
                    if issue.code
                    in {
                        RiskIssueCode.UNSUPPORTED_MARGIN_MODE,
                        RiskIssueCode.UNSUPPORTED_QUOTE_ASSET,
                        RiskIssueCode.NON_BTC_OPEN_POSITION,
                    }
                ),
                calculation_issue(RiskIssueCode.UNSUPPORTED_MARGIN_MODE),
            )
            return (
                {
                    "id": config.account_id,
                    "label": config.label,
                    "status": AccountEvaluationStatus.UNSUPPORTED.value,
                    "referenceIv": reference_iv,
                    "error": issue_to_wire(error),
                    "current": current,
                    "reconciliation": reconciliation,
                    "scenarios": None,
                    "detail": None,
                    "warnings": [issue_to_wire(issue) for issue in account_warnings],
                },
                True,
            )

        if reconciliation_status is ReconciliationStatus.UNRECONCILED:
            error = next(
                iter(reconcile_issues),
                calculation_issue(RiskIssueCode.UNRECONCILED_MAINTENANCE_MARGIN),
            )
            return (
                {
                    "id": config.account_id,
                    "label": config.label,
                    "status": AccountEvaluationStatus.UNRECONCILED.value,
                    "referenceIv": reference_iv,
                    "error": issue_to_wire(error),
                    "current": current,
                    "reconciliation": reconciliation,
                    "scenarios": None,
                    "detail": None,
                    "warnings": [issue_to_wire(issue) for issue in account_warnings],
                },
                True,
            )

        detail_shift = (
            request.detail_iv_shift_vol_points
            if request.detail_account_id == config.account_id
            else None
        )
        evaluation_input = RiskEvaluationInput(
            market=public.market,
            account=normalized.snapshot,
            contracts=public.contracts,
            valuation_time_ms=request.valuation_time_ms,
            iv_shift_vol_points=0.0,
            scan_minimum=public.market.index_price * 0.2,
            scan_maximum=public.market.index_price * 2.0,
        )
        analyses = analyze_scenarios(
            evaluation_input,
            request.iv_shifts_vol_points,
            detail_iv_shift_vol_points=detail_shift,
        )
        detail_analysis = next(
            (
                analysis
                for analysis in analyses
                if detail_shift is not None
                and analysis.iv_shift_vol_points == detail_shift
            ),
            None,
        )
        contract_by_symbol = {
            contract.symbol: contract for contract in public.contracts
        }
        detail = (
            _detail_to_wire(
                detail_analysis,
                mandatory_prices=tuple(
                    contract_by_symbol[position.symbol].strike_price
                    for position in normalized.snapshot.positions
                    if position.symbol in contract_by_symbol
                ),
            )
            if detail_analysis is not None
            else None
        )

        return (
            {
                "id": config.account_id,
                "label": config.label,
                "status": AccountEvaluationStatus.OK.value,
                "referenceIv": reference_iv,
                "error": None,
                "current": current,
                "reconciliation": reconciliation,
                "scenarios": [_scenario_to_wire(item) for item in analyses],
                "detail": detail,
                "warnings": [issue_to_wire(issue) for issue in account_warnings],
            },
            True,
        )

    def evaluate(self, payload: Any) -> dict[str, Any]:
        if self.registry.issues:
            raise RiskServiceError(self.registry.issues)
        request = validate_evaluate_request(payload, self.registry)
        selected = tuple(
            self.registry.find(account_id) for account_id in request.account_ids
        )
        configs = tuple(account for account in selected if account is not None)
        configured = tuple(
            account
            for account in configs
            if account.status is AccountConfigStatus.CONFIGURED
        )
        if not configured:
            raise RiskServiceError(
                tuple(
                    account.issue
                    or calculation_issue(
                        RiskIssueCode.MISSING_CREDENTIALS,
                        accountId=account.account_id,
                    )
                    for account in configs
                )
            )

        try:
            public = self.public_cache.fetch()
        except BinancePrivateError as error:
            raise RiskServiceError(
                (calculation_issue(error.issue_code, endpoint=error.endpoint),)
            ) from error

        now_ms = int(self._now_seconds() * 1000)
        configured_results: dict[str, tuple[dict[str, Any], bool]] = {}
        with ThreadPoolExecutor(max_workers=min(len(configured), 8)) as executor:
            futures = {
                account.account_id: executor.submit(
                    self._evaluate_account,
                    account,
                    request,
                    public,
                    now_ms,
                )
                for account in configured
            }
            for account_id, future in futures.items():
                configured_results[account_id] = future.result()

        accounts: list[dict[str, Any]] = []
        upstream_available = False
        for config in configs:
            if config.status is not AccountConfigStatus.CONFIGURED:
                issue = config.issue or calculation_issue(
                    RiskIssueCode.MISSING_CREDENTIALS,
                    accountId=config.account_id,
                )
                accounts.append(self._failure_account(config, issue))
                continue
            result, available = configured_results[config.account_id]
            accounts.append(result)
            upstream_available = upstream_available or available

        if not upstream_available:
            issues = tuple(
                calculation_issue(
                    RiskIssueCode.UPSTREAM_UNAVAILABLE,
                    accountId=config.account_id,
                )
                for config in configured
            )
            raise RiskServiceError(issues)

        global_warnings = [
            calculation_issue(RiskIssueCode.OPEN_ORDERS_NOT_INCLUDED)
        ]
        if public.market.stale:
            global_warnings.append(
                calculation_issue(RiskIssueCode.MARKET_DATA_STALE)
            )
        return {
            "updatedAt": now_ms,
            "market": {
                "indexPrice": public.market.index_price,
                "updatedAt": public.market.source_time_ms,
                "stale": public.market.stale,
            },
            "valuationTimeMs": request.valuation_time_ms,
            "scan": {
                "minimum": public.market.index_price * 0.2,
                "maximum": public.market.index_price * 2.0,
                "sampleCount": DEFAULT_SCAN_SAMPLE_COUNT,
                "boundaryTolerance": DEFAULT_BOUNDARY_TOLERANCE,
            },
            "accounts": accounts,
            "warnings": [
                issue_to_wire(issue) for issue in sorted_issues(global_warnings)
            ],
        }


def environment_cache_settings(environ: dict[str, str] | None = None) -> tuple[float, float]:
    values = os.environ if environ is None else environ
    try:
        ttl = float(values.get("BINANCE_OPTIONS_ACCOUNT_CACHE_TTL_SECONDS", "10"))
        stale = float(values.get("BINANCE_OPTIONS_ACCOUNT_STALE_MAX_SECONDS", "60"))
    except ValueError:
        return 10.0, 60.0
    if not math.isfinite(ttl) or ttl < 0 or not math.isfinite(stale) or stale < 0:
        return 10.0, 60.0
    return ttl, stale
