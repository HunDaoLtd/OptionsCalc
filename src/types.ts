export type YieldMode = "actual" | "cashSecured";

export interface OptionContract {
  symbol: string;
  expiry: number;
  expiryLabel: string;
  strike: number;
  side: "PUT" | "CALL";
  markPrice: number | null;
  markIV: number | null;
  daysToExpiry: number;
}

export interface OptionSnapshot {
  underlying: "BTC";
  indexPrice: number | null;
  serverTime: number;
  updatedAt: number;
  contracts: OptionContract[];
  stale: boolean;
  sourceError?: string;
}

export interface MatrixCell {
  contract: OptionContract | null;
  premium: number;
  netPremium: number;
  actualYield: number | null;
  cashSecuredYield: number | null;
}

export interface MatrixRow {
  expiry: number;
  expiryLabel: string;
  daysToExpiry: number;
  cells: Record<number, MatrixCell>;
}

export const RISK_ACCOUNT_CONFIG_STATUSES = [
  "configured",
  "missing_credentials",
  "configuration_error"
] as const;
export type RiskAccountConfigStatus = (typeof RISK_ACCOUNT_CONFIG_STATUSES)[number];

export const RISK_ACCOUNT_EVALUATION_STATUSES = [
  "ok",
  "unreconciled",
  "unsupported",
  "unavailable"
] as const;
export type RiskAccountEvaluationStatus = (typeof RISK_ACCOUNT_EVALUATION_STATUSES)[number];

export const RISK_RECONCILIATION_STATUSES = [
  "matched",
  "warning",
  "unreconciled",
  "unsupported"
] as const;
export type RiskReconciliationStatus = (typeof RISK_RECONCILIATION_STATUSES)[number];

export const RISK_SCENARIO_STATUSES = ["ok", "unavailable"] as const;
export type RiskScenarioStatus = (typeof RISK_SCENARIO_STATUSES)[number];

export const RISK_TRIGGER_REASONS = [
  "margin_ratio_threshold",
  "non_positive_adjusted_equity",
  "negative_wallet_long_value_ratio"
] as const;
export type RiskTriggerReason = (typeof RISK_TRIGGER_REASONS)[number];

export const RISK_TRIGGER_DIRECTIONS = ["lower", "upper"] as const;
export type RiskTriggerDirection = (typeof RISK_TRIGGER_DIRECTIONS)[number];

export const RISK_ISSUE_CODES = [
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
  "invalid_force_refresh"
] as const;
export type RiskIssueCode = (typeof RISK_ISSUE_CODES)[number];

export const RISK_API_ERROR_CODES = ["invalid_request", "service_unavailable"] as const;
export type RiskApiErrorCode = (typeof RISK_API_ERROR_CODES)[number];

export const RISK_AUTH_ERROR_CODES = [
  "authentication_required",
  "rate_limited",
  "auth_not_configured",
  "auth_unavailable",
  "origin_not_allowed"
] as const;
export type RiskAuthErrorCode = (typeof RISK_AUTH_ERROR_CODES)[number];

export interface RiskAuthSessionResponse {
  authenticated: boolean;
}

export type RiskIssueContextValue = string | number | boolean | null;

export interface RiskIssue {
  code: RiskIssueCode;
  context?: Record<string, RiskIssueContextValue>;
}

export interface RiskAccountMetadata {
  id: string;
  label: string;
  status: RiskAccountConfigStatus;
}

export interface LiquidationRiskAccountsResponse {
  defaultAccountId: string;
  accounts: RiskAccountMetadata[];
}

interface LiquidationRiskEvaluateRequestBase {
  accountIds: string[];
  valuationTimeMs: number;
  ivShiftsVolPoints: number[];
  forceRefresh?: boolean;
}

export type LiquidationRiskEvaluateRequest = LiquidationRiskEvaluateRequestBase &
  (
    | {
        detailAccountId?: never;
        detailIvShiftVolPoints?: never;
      }
    | {
        detailAccountId: string;
        detailIvShiftVolPoints: number;
      }
  );

export interface RiskMarketResponse {
  indexPrice: number;
  updatedAt: number;
  stale: boolean;
}

export interface RiskScanResponse {
  minimum: number;
  maximum: number;
  sampleCount: number;
  boundaryTolerance: number;
}

export interface RiskCurrentMetrics {
  adjustedEquity: number;
  maintenanceMargin: number;
  marginRatio: number | null;
  derivedWalletBalance: number;
  longOptionValue: number;
  unrealizedPnl: number;
  sourceTime: number;
  stale: boolean;
  triggeredNow: boolean;
  triggerReason: RiskTriggerReason | null;
}

export interface RiskReconciliation {
  status: RiskReconciliationStatus;
  mmDriftPercent: number | null;
  coveredPositions: number;
  totalPositions: number;
  sourceSkewMs: number;
}

export interface RiskTriggerBoundary {
  price: number;
  direction: RiskTriggerDirection;
  reason: RiskTriggerReason;
}

export interface RiskScenario {
  ivShiftVolPoints: number;
  status: RiskScenarioStatus;
  lowerTrigger: number | null;
  upperTrigger: number | null;
  triggers: RiskTriggerBoundary[];
  warnings: RiskIssue[];
}

export interface RiskCurvePoint {
  indexPrice: number;
  adjustedEquity: number;
  maintenanceMargin: number;
  marginRatio: number | null;
  triggered: boolean;
  triggerReason: RiskTriggerReason | null;
}

export interface RiskMaintenanceMarginContribution {
  symbol: string;
  optionSide: "CALL" | "PUT";
  positionSide: "LONG" | "SHORT";
  quantity: number;
  entryPrice: number | null;
  scenarioOptionPrice: number;
  maintenanceMargin: number;
  maintenanceMarginShare: number;
  expiryMs: number;
}

export interface RiskAccountDetail {
  ivShiftVolPoints: number;
  breakEvenPrices: number[] | null;
  remainingPositionCount: number;
  curve: RiskCurvePoint[];
  maintenanceMarginContributions: RiskMaintenanceMarginContribution[];
  triggers: RiskTriggerBoundary[];
  warnings: RiskIssue[];
}

export interface RiskAccountEvaluation {
  id: string;
  label: string;
  status: RiskAccountEvaluationStatus;
  referenceIv: number | null;
  error: RiskIssue | null;
  current: RiskCurrentMetrics | null;
  reconciliation: RiskReconciliation | null;
  scenarios: RiskScenario[] | null;
  detail: RiskAccountDetail | null;
  warnings: RiskIssue[];
}

export interface LiquidationRiskEvaluateResponse {
  updatedAt: number;
  market: RiskMarketResponse;
  valuationTimeMs: number;
  scan: RiskScanResponse;
  accounts: RiskAccountEvaluation[];
  warnings: RiskIssue[];
}

export interface RiskApiErrorResponse {
  error: {
    code: RiskApiErrorCode;
    issues: RiskIssue[];
  };
}
