from dataclasses import dataclass
from enum import StrEnum
from typing import NotRequired, TypedDict


class AccountConfigStatus(StrEnum):
    CONFIGURED = "configured"
    MISSING_CREDENTIALS = "missing_credentials"
    CONFIGURATION_ERROR = "configuration_error"


class AccountEvaluationStatus(StrEnum):
    OK = "ok"
    UNRECONCILED = "unreconciled"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class ReconciliationStatus(StrEnum):
    MATCHED = "matched"
    WARNING = "warning"
    UNRECONCILED = "unreconciled"
    UNSUPPORTED = "unsupported"


class ScenarioStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


class OptionSide(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class TriggerReason(StrEnum):
    MARGIN_RATIO_THRESHOLD = "margin_ratio_threshold"
    NON_POSITIVE_ADJUSTED_EQUITY = "non_positive_adjusted_equity"
    NEGATIVE_WALLET_LONG_VALUE_RATIO = "negative_wallet_long_value_ratio"


class TriggerDirection(StrEnum):
    LOWER = "lower"
    UPPER = "upper"


class RiskIssueCode(StrEnum):
    OPEN_ORDERS_NOT_INCLUDED = "open_orders_not_included"
    MARKET_DATA_STALE = "market_data_stale"
    ACCOUNT_DATA_STALE = "account_data_stale"
    RECONCILIATION_DRIFT_WARNING = "reconciliation_drift_warning"
    EXPIRY_SETTLEMENT_APPROXIMATION = "expiry_settlement_approximation"
    NEAR_EXPIRY_TRIGGER_DISABLED = "near_expiry_trigger_disabled"
    EXPIRED_POSITION_SETTLED = "expired_position_settled"
    MISSING_CREDENTIALS = "missing_credentials"
    CONFIGURATION_ERROR = "configuration_error"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    STALE_DATA_EXPIRED = "stale_data_expired"
    UNSUPPORTED_MARGIN_MODE = "unsupported_margin_mode"
    UNSUPPORTED_QUOTE_ASSET = "unsupported_quote_asset"
    NON_BTC_OPEN_POSITION = "non_btc_open_position"
    UNRECONCILED_MAINTENANCE_MARGIN = "unreconciled_maintenance_margin"
    MISSING_CONTRACT = "missing_contract"
    MISSING_MARK = "missing_mark"
    INVALID_NUMERIC_VALUE = "invalid_numeric_value"
    NON_POSITIVE_SCENARIO_VOLATILITY = "non_positive_scenario_volatility"
    NO_ELIGIBLE_LONG_OPTION_VALUE = "no_eligible_long_option_value"
    INVALID_JSON = "invalid_json"
    INVALID_ACCOUNT_IDS = "invalid_account_ids"
    UNKNOWN_ACCOUNT_ID = "unknown_account_id"
    INVALID_VALUATION_TIME = "invalid_valuation_time"
    INVALID_IV_SHIFTS = "invalid_iv_shifts"
    INVALID_DETAIL_ACCOUNT_ID = "invalid_detail_account_id"
    INVALID_DETAIL_IV_SHIFT = "invalid_detail_iv_shift"
    INVALID_FORCE_REFRESH = "invalid_force_refresh"


class ApiErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    SERVICE_UNAVAILABLE = "service_unavailable"


@dataclass(frozen=True, slots=True)
class RiskMarketSnapshot:
    index_price: float
    source_time_ms: int
    stale: bool


@dataclass(frozen=True, slots=True)
class RiskContract:
    symbol: str
    underlying: str
    quote_asset: str
    option_side: OptionSide
    expiry_ms: int
    strike_price: float
    unit: float | None
    maintenance_margin_rate: float | None
    min_maintenance_margin_rate: float | None
    liquidation_fee_rate: float | None
    mark_price: float | None
    mark_iv: float | None
    risk_free_interest: float | None


@dataclass(frozen=True, slots=True)
class RiskPosition:
    symbol: str
    position_side: PositionSide
    quantity: float
    entry_price: float | None
    mark_value: float
    unrealized_pnl: float
    source_time_ms: int


@dataclass(frozen=True, slots=True)
class RiskAccountSnapshot:
    account_id: str
    label: str
    official_adjusted_equity: float
    official_maintenance_margin: float
    unrealized_pnl: float
    positions: tuple[RiskPosition, ...]
    source_time_ms: int
    stale: bool


@dataclass(frozen=True, slots=True)
class RiskEvaluationInput:
    market: RiskMarketSnapshot
    account: RiskAccountSnapshot
    contracts: tuple[RiskContract, ...]
    valuation_time_ms: int
    iv_shift_vol_points: float
    scan_minimum: float
    scan_maximum: float


IssueContextValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class BlackScholesResult:
    d1: float
    d2: float
    call_price: float
    put_price: float


@dataclass(frozen=True, slots=True)
class SettlementResult:
    payoff_per_contract: float
    exercise_fee: float
    settlement_cashflow: float


@dataclass(frozen=True, slots=True)
class CalculationIssue:
    code: RiskIssueCode
    context: tuple[tuple[str, IssueContextValue], ...] = ()


@dataclass(frozen=True, slots=True)
class TriggerEvaluation:
    triggered: bool | None
    reason: TriggerReason | None
    margin_ratio: float | None
    issue: CalculationIssue | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceMarginContributionCalculation:
    symbol: str
    option_side: OptionSide
    position_side: PositionSide
    quantity: float
    entry_price: float | None
    scenario_option_price: float
    maintenance_margin: float
    maintenance_margin_share: float
    expiry_ms: int


@dataclass(frozen=True, slots=True)
class CurrentAccountCalculation:
    available: bool
    derived_wallet_balance: float | None
    long_option_value: float | None
    model_maintenance_margin: float | None
    margin_ratio: float | None
    trigger: TriggerEvaluation | None
    contributions: tuple[MaintenanceMarginContributionCalculation, ...] = ()
    issues: tuple[CalculationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ScenarioPointCalculation:
    available: bool
    index_price: float | None
    adjusted_equity: float | None
    maintenance_margin: float | None
    margin_ratio: float | None
    derived_wallet_balance: float | None
    eligible_long_option_value: float | None
    settlement_cashflow: float | None
    triggered: bool | None
    trigger_reason: TriggerReason | None
    unrealized_pnl: float | None = None
    contributions: tuple[MaintenanceMarginContributionCalculation, ...] = ()
    issues: tuple[CalculationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class TriggerBoundaryCalculation:
    price: float
    direction: TriggerDirection
    reason: TriggerReason


@dataclass(frozen=True, slots=True)
class RootSearchResult:
    available: bool
    curve: tuple[ScenarioPointCalculation, ...]
    triggers: tuple[TriggerBoundaryCalculation, ...]
    issues: tuple[CalculationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ScenarioAnalysis:
    status: ScenarioStatus
    iv_shift_vol_points: float | None
    current_point: ScenarioPointCalculation | None
    curve: tuple[ScenarioPointCalculation, ...]
    triggers: tuple[TriggerBoundaryCalculation, ...]
    lower_trigger: float | None
    upper_trigger: float | None
    break_even_prices: tuple[float, ...] | None = None
    remaining_position_count: int | None = None
    issues: tuple[CalculationIssue, ...] = ()


class RiskIssue(TypedDict):
    code: RiskIssueCode
    context: NotRequired[dict[str, IssueContextValue]]


class RiskAccountMetadata(TypedDict):
    id: str
    label: str
    status: AccountConfigStatus


class LiquidationRiskAccountsResponse(TypedDict):
    defaultAccountId: str
    accounts: list[RiskAccountMetadata]


class LiquidationRiskEvaluateRequest(TypedDict):
    accountIds: list[str]
    valuationTimeMs: int
    ivShiftsVolPoints: list[float]
    detailAccountId: NotRequired[str]
    detailIvShiftVolPoints: NotRequired[float]
    forceRefresh: NotRequired[bool]


class RiskMarketResponse(TypedDict):
    indexPrice: float
    updatedAt: int
    stale: bool


class RiskScanResponse(TypedDict):
    minimum: float
    maximum: float
    sampleCount: int
    boundaryTolerance: float


class RiskCurrentMetrics(TypedDict):
    adjustedEquity: float
    maintenanceMargin: float
    marginRatio: float | None
    derivedWalletBalance: float
    longOptionValue: float
    unrealizedPnl: float
    sourceTime: int
    stale: bool
    triggeredNow: bool
    triggerReason: TriggerReason | None


class RiskReconciliation(TypedDict):
    status: ReconciliationStatus
    mmDriftPercent: float | None
    coveredPositions: int
    totalPositions: int
    sourceSkewMs: int


class RiskTriggerBoundary(TypedDict):
    price: float
    direction: TriggerDirection
    reason: TriggerReason


class RiskScenario(TypedDict):
    ivShiftVolPoints: float
    status: ScenarioStatus
    lowerTrigger: float | None
    upperTrigger: float | None
    triggers: list[RiskTriggerBoundary]
    warnings: list[RiskIssue]


class RiskCurvePoint(TypedDict):
    indexPrice: float
    adjustedEquity: float
    maintenanceMargin: float
    marginRatio: float | None
    triggered: bool
    triggerReason: TriggerReason | None


class RiskMaintenanceMarginContribution(TypedDict):
    symbol: str
    optionSide: OptionSide
    positionSide: PositionSide
    quantity: float
    entryPrice: float | None
    scenarioOptionPrice: float
    maintenanceMargin: float
    maintenanceMarginShare: float
    expiryMs: int


class RiskAccountDetail(TypedDict):
    ivShiftVolPoints: float
    breakEvenPrices: list[float] | None
    remainingPositionCount: int
    curve: list[RiskCurvePoint]
    maintenanceMarginContributions: list[RiskMaintenanceMarginContribution]
    triggers: list[RiskTriggerBoundary]
    warnings: list[RiskIssue]


class RiskAccountEvaluation(TypedDict):
    id: str
    label: str
    status: AccountEvaluationStatus
    referenceIv: float | None
    error: RiskIssue | None
    current: RiskCurrentMetrics | None
    reconciliation: RiskReconciliation | None
    scenarios: list[RiskScenario] | None
    detail: RiskAccountDetail | None
    warnings: list[RiskIssue]


class LiquidationRiskEvaluateResponse(TypedDict):
    updatedAt: int
    market: RiskMarketResponse
    valuationTimeMs: int
    scan: RiskScanResponse
    accounts: list[RiskAccountEvaluation]
    warnings: list[RiskIssue]


class RiskApiErrorDetails(TypedDict):
    code: ApiErrorCode
    issues: list[RiskIssue]


class RiskApiErrorResponse(TypedDict):
    error: RiskApiErrorDetails
