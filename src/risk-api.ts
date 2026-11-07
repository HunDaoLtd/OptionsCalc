/** @format */

import {
  RISK_ACCOUNT_CONFIG_STATUSES,
  RISK_ACCOUNT_EVALUATION_STATUSES,
  RISK_API_ERROR_CODES,
  RISK_AUTH_ERROR_CODES,
  RISK_ISSUE_CODES,
  RISK_RECONCILIATION_STATUSES,
  RISK_SCENARIO_STATUSES,
  RISK_TRIGGER_DIRECTIONS,
  RISK_TRIGGER_REASONS
} from "./types";
import type {
  LiquidationRiskAccountsResponse,
  LiquidationRiskEvaluateRequest,
  LiquidationRiskEvaluateResponse,
  RiskApiErrorCode,
  RiskAuthErrorCode,
  RiskAuthSessionResponse,
  RiskIssue,
  RiskIssueCode
} from "./types";
import { tRisk } from "./i18n";

const RISK_API_BASE = `${import.meta.env.BASE_URL}risk/api/`;

type JsonObject = Record<string, unknown>;

export class RiskApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly code: RiskApiErrorCode | RiskAuthErrorCode | "invalid_response" | "network_error",
    readonly issues: RiskIssue[] = []
  ) {
    super(message);
    this.name = "RiskApiClientError";
  }
}

export function isAuthenticationRequired(error: unknown): error is RiskApiClientError {
  return error instanceof RiskApiClientError && error.code === "authentication_required";
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0;
}

function isNullableFinite(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value);
}

function isOneOf<T extends readonly string[]>(value: unknown, values: T): value is T[number] {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

function isIssue(value: unknown): value is RiskIssue {
  if (!isObject(value) || !isOneOf(value.code, RISK_ISSUE_CODES)) return false;
  if (value.context === undefined) return true;
  if (!isObject(value.context)) return false;
  return Object.values(value.context).every(
    (item) => item === null || ["string", "number", "boolean"].includes(typeof item)
  );
}

function isIssueArray(value: unknown): value is RiskIssue[] {
  return Array.isArray(value) && value.every(isIssue);
}

function isTrigger(value: unknown) {
  return (
    isObject(value) &&
    isFiniteNumber(value.price) &&
    isOneOf(value.direction, RISK_TRIGGER_DIRECTIONS) &&
    isOneOf(value.reason, RISK_TRIGGER_REASONS)
  );
}

function isCurrent(value: unknown) {
  return (
    isObject(value) &&
    isFiniteNumber(value.adjustedEquity) &&
    isFiniteNumber(value.maintenanceMargin) &&
    isNullableFinite(value.marginRatio) &&
    isFiniteNumber(value.derivedWalletBalance) &&
    isFiniteNumber(value.longOptionValue) &&
    isFiniteNumber(value.unrealizedPnl) &&
    isFiniteNumber(value.sourceTime) &&
    typeof value.stale === "boolean" &&
    typeof value.triggeredNow === "boolean" &&
    (value.triggerReason === null || isOneOf(value.triggerReason, RISK_TRIGGER_REASONS))
  );
}

function isReconciliation(value: unknown) {
  return (
    isObject(value) &&
    isOneOf(value.status, RISK_RECONCILIATION_STATUSES) &&
    isNullableFinite(value.mmDriftPercent) &&
    Number.isInteger(value.coveredPositions) &&
    Number.isInteger(value.totalPositions) &&
    isFiniteNumber(value.sourceSkewMs)
  );
}

function isScenario(value: unknown) {
  return (
    isObject(value) &&
    isFiniteNumber(value.ivShiftVolPoints) &&
    isOneOf(value.status, RISK_SCENARIO_STATUSES) &&
    isNullableFinite(value.lowerTrigger) &&
    isNullableFinite(value.upperTrigger) &&
    Array.isArray(value.triggers) &&
    value.triggers.every(isTrigger) &&
    isIssueArray(value.warnings)
  );
}

function isCurvePoint(value: unknown) {
  return (
    isObject(value) &&
    isFiniteNumber(value.indexPrice) &&
    isFiniteNumber(value.adjustedEquity) &&
    isFiniteNumber(value.maintenanceMargin) &&
    isNullableFinite(value.marginRatio) &&
    typeof value.triggered === "boolean" &&
    (value.triggerReason === null || isOneOf(value.triggerReason, RISK_TRIGGER_REASONS))
  );
}

function isContribution(value: unknown) {
  return (
    isObject(value) &&
    typeof value.symbol === "string" &&
    isOneOf(value.optionSide, ["CALL", "PUT"] as const) &&
    isOneOf(value.positionSide, ["LONG", "SHORT"] as const) &&
    isFiniteNumber(value.quantity) &&
    isNullableFinite(value.entryPrice) &&
    isFiniteNumber(value.scenarioOptionPrice) &&
    isFiniteNumber(value.maintenanceMargin) &&
    isFiniteNumber(value.maintenanceMarginShare) &&
    isFiniteNumber(value.expiryMs)
  );
}

function isDetail(value: unknown) {
  return (
    isObject(value) &&
    isFiniteNumber(value.ivShiftVolPoints) &&
    (value.breakEvenPrices === null ||
      (Array.isArray(value.breakEvenPrices) && value.breakEvenPrices.every(isFiniteNumber))) &&
    isNonNegativeInteger(value.remainingPositionCount) &&
    Array.isArray(value.curve) &&
    value.curve.every(isCurvePoint) &&
    Array.isArray(value.maintenanceMarginContributions) &&
    value.maintenanceMarginContributions.every(isContribution) &&
    Array.isArray(value.triggers) &&
    value.triggers.every(isTrigger) &&
    isIssueArray(value.warnings)
  );
}

export function parseRiskAccountsResponse(value: unknown): LiquidationRiskAccountsResponse {
  if (
    !isObject(value) ||
    typeof value.defaultAccountId !== "string" ||
    !Array.isArray(value.accounts) ||
    !value.accounts.every(
      (account) =>
        isObject(account) &&
        typeof account.id === "string" &&
        typeof account.label === "string" &&
        isOneOf(account.status, RISK_ACCOUNT_CONFIG_STATUSES)
    )
  ) {
    throw new RiskApiClientError("The accounts response is not valid.", null, "invalid_response");
  }
  return value as unknown as LiquidationRiskAccountsResponse;
}

export function parseRiskEvaluateResponse(value: unknown): LiquidationRiskEvaluateResponse {
  const valid =
    isObject(value) &&
    isFiniteNumber(value.updatedAt) &&
    isObject(value.market) &&
    isFiniteNumber(value.market.indexPrice) &&
    isFiniteNumber(value.market.updatedAt) &&
    typeof value.market.stale === "boolean" &&
    isFiniteNumber(value.valuationTimeMs) &&
    isObject(value.scan) &&
    isFiniteNumber(value.scan.minimum) &&
    isFiniteNumber(value.scan.maximum) &&
    Number.isInteger(value.scan.sampleCount) &&
    isFiniteNumber(value.scan.boundaryTolerance) &&
    Array.isArray(value.accounts) &&
    value.accounts.every(
      (account) =>
        isObject(account) &&
        typeof account.id === "string" &&
        typeof account.label === "string" &&
        isOneOf(account.status, RISK_ACCOUNT_EVALUATION_STATUSES) &&
        isNullableFinite(account.referenceIv) &&
        (account.error === null || isIssue(account.error)) &&
        (account.current === null || isCurrent(account.current)) &&
        (account.reconciliation === null || isReconciliation(account.reconciliation)) &&
        (account.scenarios === null || (Array.isArray(account.scenarios) && account.scenarios.every(isScenario))) &&
        (account.detail === null || isDetail(account.detail)) &&
        isIssueArray(account.warnings)
    ) &&
    isIssueArray(value.warnings);

  if (!valid) {
    throw new RiskApiClientError("The risk response is not valid.", null, "invalid_response");
  }
  return value as unknown as LiquidationRiskEvaluateResponse;
}

function parseErrorResponse(value: unknown): { code: RiskApiErrorCode; issues: RiskIssue[] } | null {
  if (
    !isObject(value) ||
    !isObject(value.error) ||
    !isOneOf(value.error.code, RISK_API_ERROR_CODES) ||
    !isIssueArray(value.error.issues)
  ) {
    return null;
  }
  return { code: value.error.code, issues: value.error.issues };
}

function parseAuthErrorCode(value: unknown): RiskAuthErrorCode | null {
  if (!isObject(value) || !isObject(value.error) || !isOneOf(value.error.code, RISK_AUTH_ERROR_CODES)) {
    return null;
  }
  return value.error.code;
}

function statusAuthError(status: number): RiskAuthErrorCode | null {
  if (status === 401) return "authentication_required";
  if (status === 429) return "rate_limited";
  return null;
}

function authErrorMessage(code: RiskAuthErrorCode) {
  const messages: Record<RiskAuthErrorCode, string> = {
    authentication_required: "Enter the Risk access password to continue.",
    rate_limited: "Too many login attempts. Wait before trying again.",
    auth_not_configured: "Risk access is temporarily locked while authentication is configured.",
    auth_unavailable: "Risk authentication is temporarily unavailable.",
    origin_not_allowed: "The Risk login request did not come from this site."
  };
  return messages[code];
}

async function requestJson(
  path: string,
  init: RequestInit,
  parse: (value: unknown) => LiquidationRiskAccountsResponse | LiquidationRiskEvaluateResponse
) {
  let response: Response;
  try {
    response = await fetch(`${RISK_API_BASE}${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...init.headers }
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new RiskApiClientError("Unable to reach the risk service.", null, "network_error");
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      const authCode = statusAuthError(response.status);
      if (authCode) {
        throw new RiskApiClientError(authErrorMessage(authCode), response.status, authCode);
      }
      throw new RiskApiClientError("The risk service returned an unreadable response.", response.status, "invalid_response");
    }
  }

  if (!response.ok) {
    const authCode = parseAuthErrorCode(payload) ?? statusAuthError(response.status);
    if (authCode) {
      throw new RiskApiClientError(authErrorMessage(authCode), response.status, authCode);
    }
    const apiError = parseErrorResponse(payload);
    if (apiError) {
      throw new RiskApiClientError(
        apiError.code === "invalid_request" ? "The risk request was rejected." : "The risk service is unavailable.",
        response.status,
        apiError.code,
        apiError.issues
      );
    }
    throw new RiskApiClientError("The risk service is unavailable.", response.status, "invalid_response");
  }
  return parse(payload);
}

async function requestRiskAuth(
  path: "session" | "login" | "logout",
  init: RequestInit
): Promise<RiskAuthSessionResponse | null> {
  let response: Response;
  try {
    response = await fetch(`${RISK_API_BASE}auth/${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...init.headers }
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new RiskApiClientError("Unable to reach Risk authentication.", null, "network_error");
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      const authCode = statusAuthError(response.status);
      if (authCode) throw new RiskApiClientError(authErrorMessage(authCode), response.status, authCode);
      throw new RiskApiClientError("Risk authentication returned an unreadable response.", response.status, "invalid_response");
    }
  }
  if (!response.ok) {
    const authCode = parseAuthErrorCode(payload) ?? statusAuthError(response.status);
    if (authCode) throw new RiskApiClientError(authErrorMessage(authCode), response.status, authCode);
    throw new RiskApiClientError("Risk authentication is temporarily unavailable.", response.status, "auth_unavailable");
  }
  if (path === "logout") return null;
  if (!isObject(payload) || typeof payload.authenticated !== "boolean") {
    throw new RiskApiClientError("The Risk authentication response is invalid.", response.status, "invalid_response");
  }
  return payload as unknown as RiskAuthSessionResponse;
}

export function getRiskAuthSession(signal: AbortSignal) {
  return requestRiskAuth("session", { method: "GET", signal }) as Promise<RiskAuthSessionResponse>;
}

export function loginRisk(password: string, signal: AbortSignal) {
  return requestRiskAuth("login", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  }) as Promise<RiskAuthSessionResponse>;
}

export async function logoutRisk(signal: AbortSignal) {
  await requestRiskAuth("logout", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: "{}"
  });
}

export function getRiskAccounts(signal: AbortSignal) {
  return requestJson("accounts", { method: "GET", signal }, parseRiskAccountsResponse) as Promise<LiquidationRiskAccountsResponse>;
}

export function evaluateRisk(request: LiquidationRiskEvaluateRequest, signal: AbortSignal) {
  return requestJson(
    "evaluate",
    {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request)
    },
    parseRiskEvaluateResponse
  ) as Promise<LiquidationRiskEvaluateResponse>;
}

export function issueMessage(code: RiskIssueCode) {
  return tRisk(`issue.${code}`);
}
