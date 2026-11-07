/** @format */

import React from "react";
import { Info, LockKeyhole, Plus, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { activeLocale, tRisk } from "./i18n";
import {
  evaluateRisk,
  getRiskAccounts,
  getRiskAuthSession,
  isAuthenticationRequired,
  issueMessage,
  loginRisk,
  logoutRisk,
  RiskApiClientError
} from "./risk-api";
import { formatNumber, ToolkitPageShell, UpdatedAgo } from "./toolkit";
import type {
  LiquidationRiskAccountsResponse,
  LiquidationRiskEvaluateRequest,
  LiquidationRiskEvaluateResponse,
  RiskAccountEvaluation,
  RiskCurvePoint,
  RiskIssue,
  RiskScenario,
  RiskTriggerBoundary
} from "./types";

const REFRESH_MS = 30_000;
const REQUEST_TIMEOUT_MS = 30_000;
const IV_SHIFTS = Array.from({ length: 21 }, (_, index) => -50 + index * 10);
const MIN_TRIGGER_CHART_WIDTH = 1_100;
const VISIBLE_ACCOUNTS_KEY = "btc-options-risk-visible-accounts-v1";
const ACTIVE_ACCOUNT_KEY = "btc-options-risk-active-account-v1";
const DAY_MS = 86_400_000;
const ACCOUNT_SERIES_COLORS = [
  "var(--risk-series-1)",
  "var(--risk-series-2)",
  "var(--risk-series-3)",
  "var(--risk-series-4)",
  "var(--risk-series-5)",
  "var(--risk-series-6)"
] as const;

type ValuationPreset = "now" | "plus1d" | "plus7d" | "custom";
type ActiveAccountId = "all" | string;

interface AccountViewState {
  ready: boolean;
  visibleIds: string[];
  activeId: ActiveAccountId;
}

interface ValuationSelection {
  preset: ValuationPreset;
  targetTimeMs: number | null;
  customValue: string;
}

interface OverviewQuery {
  key: string;
  accountIds: string[];
  selection: ValuationSelection;
}

function errorText(error: unknown) {
  if (error instanceof RiskApiClientError) {
    const detail = error.issues[0] ? `issue.${error.issues[0].code}` : null;
    return detail ?? error.message;
  }
  if (error instanceof Error) return error.message;
  return tRisk("The risk service is unavailable.");
}

function valuationTime(selection: ValuationSelection, baseTime = Date.now()) {
  if (selection.preset === "now") return Math.trunc(baseTime);
  if (selection.targetTimeMs !== null && Number.isFinite(selection.targetTimeMs)) {
    return Math.trunc(selection.targetTimeMs);
  }
  const parsed = new Date(selection.customValue).getTime();
  return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
}

function toLocalDateTimeInput(value: number) {
  const date = new Date(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function accountFrom(response: LiquidationRiskEvaluateResponse | null, accountId: string | null) {
  return response?.accounts.find((account) => account.id === accountId) ?? null;
}

function selectedScenario(account: RiskAccountEvaluation | null, ivShift: number) {
  return account?.scenarios?.find((scenario) => scenario.ivShiftVolPoints === ivShift) ?? null;
}

function nearestBoundary(triggers: RiskTriggerBoundary[], indexPrice: number) {
  return triggers.reduce<RiskTriggerBoundary | null>((nearest, trigger) => {
    if (!nearest) return trigger;
    const distance = Math.abs(trigger.price - indexPrice);
    const nearestDistance = Math.abs(nearest.price - indexPrice);
    if (distance !== nearestDistance) return distance < nearestDistance ? trigger : nearest;
    return trigger.direction === "lower" ? trigger : nearest;
  }, null);
}

function nearestCurvePoint(curve: RiskCurvePoint[], indexPrice: number) {
  return curve.reduce<RiskCurvePoint | null>((nearest, point) => {
    if (!nearest) return point;
    return Math.abs(point.indexPrice - indexPrice) < Math.abs(nearest.indexPrice - indexPrice) ? point : nearest;
  }, null);
}

function formatRatio(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${formatNumber(value * 100, 1)}%`;
}

function formatDistance(price: number | null, indexPrice: number) {
  if (price === null || !indexPrice) return tRisk("Safe");
  const distance = ((price / indexPrice) - 1) * 100;
  return `${distance >= 0 ? "+" : ""}${formatNumber(distance, 1)}%`;
}

function formatIvShift(value: number) {
  return `${value >= 0 ? "+" : ""}${value}`;
}

function formatAbsoluteIv(referenceIv: number | null, ivShift: number) {
  if (referenceIv === null) return null;
  const shiftedIv = referenceIv * 100 + ivShift;
  return shiftedIv > 0 ? `${formatNumber(shiftedIv, 1)}%` : tRisk("N/A");
}

function averageReferenceIv(accounts: RiskAccountEvaluation[]) {
  const values = accounts.flatMap((account) => account.referenceIv === null ? [] : [account.referenceIv]);
  return values.length > 0 ? values.reduce((total, value) => total + value, 0) / values.length : null;
}

function statusLabel(account: RiskAccountEvaluation | null) {
  if (!account) return "UNAVAILABLE";
  if (account.status !== "ok") return account.status.toUpperCase();
  if (!account.current) return "UNAVAILABLE";
  return account.current.stale ? "STALE" : "LIVE";
}

function readStoredVisibleAccounts() {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(VISIBLE_ACCOUNTS_KEY) ?? "null");
    return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : null;
  } catch {
    return null;
  }
}

function readStoredActiveAccount() {
  try {
    return window.localStorage.getItem(ACTIVE_ACCOUNT_KEY) ?? "all";
  } catch {
    return "all";
  }
}

function initializeAccountView(
  registry: LiquidationRiskAccountsResponse,
  previous: AccountViewState,
  preferDefaultAccount = false
): AccountViewState {
  const knownIds = new Set(registry.accounts.map((account) => account.id));
  const sourceIds = previous.ready
    ? previous.visibleIds
    : (readStoredVisibleAccounts() ?? [registry.defaultAccountId]);
  const hasKnownSourceAccount = sourceIds.some((id) => knownIds.has(id));
  const includeDefaultAccount = sourceIds.includes(registry.defaultAccountId)
    || (!previous.ready && (preferDefaultAccount || !hasKnownSourceAccount));
  const visibleIds = [
    ...(includeDefaultAccount ? [registry.defaultAccountId] : []),
    ...sourceIds.filter((id) => id !== registry.defaultAccountId && knownIds.has(id))
  ].filter((id, index, values) => knownIds.has(id) && values.indexOf(id) === index);
  const storedActive = previous.ready
    ? previous.activeId
    : readStoredActiveAccount();
  const nextState: AccountViewState = {
    ready: true,
    visibleIds,
    activeId: storedActive === "all" || visibleIds.includes(storedActive) ? storedActive : "all"
  };
  if (
    previous.ready &&
    previous.activeId === nextState.activeId &&
    previous.visibleIds.length === visibleIds.length &&
    previous.visibleIds.every((id, index) => id === visibleIds[index])
  ) {
    return previous;
  }
  return nextState;
}

function makeValuationSelection(preset: ValuationPreset, customValue: string, baseTime = Date.now()): ValuationSelection {
  if (preset === "now") return { preset, targetTimeMs: null, customValue };
  if (preset === "plus1d") return { preset, targetTimeMs: Math.trunc(baseTime + DAY_MS), customValue };
  if (preset === "plus7d") return { preset, targetTimeMs: Math.trunc(baseTime + 7 * DAY_MS), customValue };
  const parsed = new Date(customValue).getTime();
  return { preset, targetTimeMs: Number.isFinite(parsed) ? Math.trunc(parsed) : null, customValue };
}

function valuationKey(selection: ValuationSelection) {
  return selection.preset === "now" ? "now" : `${selection.preset}:${selection.targetTimeMs ?? "invalid"}`;
}

function formatScanRange(minimum: number | undefined, maximum: number | undefined) {
  if (minimum === undefined || maximum === undefined) return tRisk("Scan range unavailable");
  return tRisk("scan range", { minimum: formatNumber(minimum), maximum: formatNumber(maximum) });
}

function overviewStatus(response: LiquidationRiskEvaluateResponse | null, requestedCount: number) {
  if (!response) return "UNAVAILABLE";
  if (response.market.stale) return "STALE";
  const liveAccounts = response.accounts.filter(
    (account) => account.status === "ok" && account.current && !account.current.stale
  ).length;
  return liveAccounts === requestedCount ? "LIVE" : liveAccounts > 0 ? "PARTIAL" : "UNAVAILABLE";
}

function mergeIssues(...groups: Array<RiskIssue[] | undefined | null>) {
  const seen = new Set<string>();
  return groups.flatMap((group) => group ?? []).filter((issue) => {
    const key = issue.code;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

interface AccountCardProps {
  metadataLabel: string;
  account: RiskAccountEvaluation | null;
  scenario: RiskScenario | null;
  response: LiquidationRiskEvaluateResponse | null;
  loading: boolean;
  error: string | null;
  onOpen: () => void;
  buttonRef: (element: HTMLButtonElement | null) => void;
}

function AccountCard({
  metadataLabel,
  account,
  scenario,
  response,
  loading,
  error,
  onOpen,
  buttonRef
}: AccountCardProps) {
  const current = account?.current;
  const indexPrice = response?.market.indexPrice ?? 0;
  const ratioPercent = current?.marginRatio === null || current?.marginRatio === undefined
    ? null
    : Math.max(0, Math.min(100, current.marginRatio * 100));
  const unavailable = !current || !scenario || scenario.status !== "ok";
  const accountError = account?.error ? issueMessage(account.error.code) : null;
  const scenarioError = scenario?.status === "unavailable" && scenario.warnings[0]
    ? issueMessage(scenario.warnings[0].code)
    : null;
  const scanRange = formatScanRange(response?.scan.minimum, response?.scan.maximum);
  const elevatedRisk = current?.marginRatio !== null
    && current?.marginRatio !== undefined
    && current.marginRatio > 0.8;
  const riskLevelStyle = ratioPercent === null ? undefined : {
    "--risk-level": `${ratioPercent}%`
  } as React.CSSProperties;

  return (
    <article className="risk-card-shell">
      <button
        ref={buttonRef}
        type="button"
        className={`risk-account-card${elevatedRisk ? " elevated-risk" : ""}${current?.triggeredNow ? " triggered" : ""}`}
        style={riskLevelStyle}
        onClick={onOpen}
        aria-label={tRisk("Open account risk details", { account: account?.label ?? metadataLabel })}
      >
        <span className="risk-water-zone">
          {ratioPercent !== null ? (
            <>
              <span className="risk-reservoir" />
              <svg
                className="risk-ripple"
                viewBox="0 0 120 8"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <path d="M0 4 C5 1 10 1 15 4 S25 7 30 4 S40 1 45 4 S55 7 60 4 S70 1 75 4 S85 7 90 4 S100 1 105 4 S115 7 120 4" />
              </svg>
              <span className="risk-waterline">
                {formatRatio(current?.marginRatio)}
              </span>
            </>
          ) : null}
          <span className="risk-threshold risk-threshold-95"><span>{tRisk("95% Liquidation")}</span></span>
          <span className="risk-threshold risk-threshold-80"><span>{tRisk("80% Alert")}</span></span>
          <span className="risk-card-caption">{tRisk("Margin Ratio")}</span>
        </span>
        <span className="risk-card-content">
          <span className="risk-card-heading">
            <strong>{account?.label ?? metadataLabel}</strong>
            <span
              className={`live-indicator ${statusLabel(account).toLowerCase()}`}
              role="img"
              aria-label={tRisk("API status", { status: tRisk(statusLabel(account)) })}
              title={tRisk("API status", { status: tRisk(statusLabel(account)) })}
            />
            <small className="risk-card-updated">{loading ? tRisk("Refreshing…") : <UpdatedAgo timestamp={current?.sourceTime} />}</small>
          </span>
          <span className="risk-card-caption-slot" aria-hidden="true" />
          {current?.triggeredNow ? (
            <span className="risk-triggered-now">{tRisk("Triggered now")}</span>
          ) : unavailable ? (
            <span className="risk-unavailable-result">{tRisk("Scenario unavailable")}</span>
          ) : (
            <span className="risk-boundary-grid">
              <span className={scenario.lowerTrigger === null ? "empty" : "has-trigger"}>
                <small>{tRisk("Lower Trigger")}</small>
                <strong>{scenario.lowerTrigger === null ? tRisk("Safe") : formatNumber(scenario.lowerTrigger)}</strong>
                <em>{scenario.lowerTrigger === null ? scanRange : tRisk("distance from index", { distance: formatDistance(scenario.lowerTrigger, indexPrice) })}</em>
              </span>
              <span className={scenario.upperTrigger === null ? "empty" : "has-trigger"}>
                <small>{tRisk("Upper Trigger")}</small>
                <strong>{scenario.upperTrigger === null ? tRisk("Safe") : formatNumber(scenario.upperTrigger)}</strong>
                <em>{scenario.upperTrigger === null ? scanRange : tRisk("distance from index", { distance: formatDistance(scenario.upperTrigger, indexPrice) })}</em>
              </span>
            </span>
          )}
          <span className="risk-card-metrics">
            <span><small>{tRisk("Adjusted Equity")}</small><strong>{formatNumber(current?.adjustedEquity)}</strong></span>
            <span><small>{tRisk("Maint. Margin")}</small><strong>{formatNumber(current?.maintenanceMargin)}</strong></span>
          </span>
          {error || accountError || scenarioError ? <span className="inline-alert">{error ?? accountError ?? scenarioError}</span> : null}
          {account?.status === "unreconciled" ? <span className="inline-alert">{tRisk("Scenario results are blocked until MM reconciles.")}</span> : null}
        </span>
      </button>
    </article>
  );
}

interface TriggerBoundsChartProps {
  accounts: RiskAccountEvaluation[];
  accountColors: Map<string, string>;
  indexPrice: number;
  scanMinimum: number;
  scanMaximum: number;
}

interface BoundarySeries {
  accountId: string;
  label: string;
  direction: "lower" | "upper";
  color: string;
  referenceIv: number | null;
  values: Array<number | null>;
}

function splitBoundarySegments(values: Array<number | null>) {
  const segments: Array<Array<{ index: number; value: number }>> = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (segments.at(-1)?.length) segments.push([]);
      return;
    }
    if (segments.length === 0 || segments.at(-1)?.length === 0) segments.push([{ index, value }]);
    else segments.at(-1)?.push({ index, value });
  });
  return segments.filter((segment) => segment.length > 0);
}

function shouldShowBoundaryLabel(values: Array<number | null>, index: number) {
  const value = values[index];
  if (value === null) return false;
  const previous = values[index - 1] ?? null;
  const next = values[index + 1] ?? null;
  return previous === null
    || next === null
    || formatNumber(previous) !== formatNumber(value);
}

function firstBoundaryPointIndex(values: Array<number | null>) {
  return values.findIndex((value) => value !== null);
}

function TriggerBoundsChart({ accounts, accountColors, indexPrice, scanMinimum, scanMaximum }: TriggerBoundsChartProps) {
  const chartScrollRef = React.useRef<HTMLDivElement>(null);
  const [{ width, height }, setDimensions] = React.useState({ width: MIN_TRIGGER_CHART_WIDTH, height: 250 });
  const [axisTitleX, setAxisTitleX] = React.useState(MIN_TRIGGER_CHART_WIDTH / 2);
  const margin = { left: 154, right: 32, top: 24, bottom: 70 };
  const yAxisLabelX = margin.left - 88;
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const referenceIv = averageReferenceIv(accounts);
  const series: BoundarySeries[] = accounts.flatMap((account) => {
    const values = (direction: "lower" | "upper") => IV_SHIFTS.map((shift) => {
      const scenario = account.scenarios?.find((item) => item.ivShiftVolPoints === shift);
      if (!scenario || scenario.status !== "ok") return null;
      return direction === "lower" ? scenario.lowerTrigger : scenario.upperTrigger;
    });
    const color = accountColors.get(account.id) ?? ACCOUNT_SERIES_COLORS[0];
    return [
      { accountId: account.id, label: account.label, direction: "lower" as const, color, referenceIv: account.referenceIv, values: values("lower") },
      { accountId: account.id, label: account.label, direction: "upper" as const, color, referenceIv: account.referenceIv, values: values("upper") }
    ];
  });
  const finiteBounds = series.flatMap((item) => item.values.flatMap((value) => value === null ? [] : [value]));
  const hasFiniteBounds = finiteBounds.length > 0;
  const referenceIvDescription = accounts.length > 1
    ? tRisk("Reference IV average")
    : tRisk("Reference IV single");
  const scaleValues = finiteBounds.length > 0 ? [...finiteBounds, indexPrice] : [scanMinimum, scanMaximum, indexPrice];
  const observedMinimum = Math.min(...scaleValues);
  const observedMaximum = Math.max(...scaleValues);
  const padding = Math.max((observedMaximum - observedMinimum) * 0.1, indexPrice * 0.025, 1);
  const minimum = Math.max(0, observedMinimum - padding);
  const maximum = observedMaximum + padding;
  const x = (index: number) => margin.left + (index / Math.max(1, IV_SHIFTS.length - 1)) * plotWidth;
  const y = (price: number) => margin.top + (1 - (price - minimum) / Math.max(1, maximum - minimum)) * plotHeight;
  const yTicks = Array.from({ length: 5 }, (_, index) => minimum + ((maximum - minimum) * index) / 4);

  const updateAxisTitlePosition = React.useCallback((container: HTMLDivElement, chartWidth: number) => {
    const visibleCenter = container.scrollLeft + (container.clientWidth - 20) / 2;
    const nextX = Math.round(Math.max(margin.left, Math.min(chartWidth - margin.right, visibleCenter)));
    setAxisTitleX((current) => current === nextX ? current : nextX);
  }, [margin.left, margin.right]);

  React.useLayoutEffect(() => {
    const container = chartScrollRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const updateDimensions = () => {
      const nextWidth = Math.max(MIN_TRIGGER_CHART_WIDTH, Math.round(container.clientWidth - 20));
      const nextHeight = Math.max(180, Math.round(container.clientHeight));
      setDimensions((current) => current.width === nextWidth && current.height === nextHeight
        ? current
        : { width: nextWidth, height: nextHeight });
      updateAxisTitlePosition(container, nextWidth);
    };
    const observer = new ResizeObserver(updateDimensions);
    observer.observe(container);
    window.addEventListener("resize", updateDimensions);
    updateDimensions();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", updateDimensions);
    };
  }, [hasFiniteBounds, updateAxisTitlePosition]);

  return (
    <article className="overview-chart-panel">
      <header className="overview-chart-header">
        <div className="overview-chart-heading">
          <div className="overview-chart-title">
            <h2>{tRisk("95% Trigger Bounds")}</h2>
            <button
              className="reference-iv-info"
              type="button"
              aria-label={tRisk("Reference IV details")}
              aria-describedby="reference-iv-tooltip"
            >
              <Info size={15} aria-hidden="true" />
            </button>
            <span className="reference-iv-tooltip" id="reference-iv-tooltip" role="tooltip">
              <span>{referenceIvDescription}</span>
              <span>{tRisk("Calculated scenarios", { count: accounts.length, scenarios: IV_SHIFTS.length })}</span>
              <span>{tRisk("Calculated value · not an execution price")}</span>
            </span>
          </div>
        </div>
        <div className="overview-chart-legend" aria-label={tRisk("Chart legend")}>
          {accounts.map((account) => (
            <span className="account-key" key={account.id} style={{ "--series-color": accountColors.get(account.id) } as React.CSSProperties}>
              <i /> {account.label}
            </span>
          ))}
          <span className="line-style-key" aria-label={tRisk("Lower trigger, solid line")}>
            <i /> {tRisk("Lower trigger")}
          </span>
          <span className="line-style-key upper-key" aria-label={tRisk("Upper trigger, dashed line")}>
            <i /> {tRisk("Upper trigger")}
          </span>
        </div>
      </header>
      {!hasFiniteBounds ? (
        <div className="overview-chart-empty">
          <strong>{tRisk("No trigger bounds in scan")}</strong>
          <span>{formatScanRange(scanMinimum, scanMaximum)}</span>
        </div>
      ) : (
        <>
          <div
            ref={chartScrollRef}
            className="overview-chart-scroll"
            onScroll={(event) => updateAxisTitlePosition(event.currentTarget, width)}
          >
            <svg
              className="overview-trigger-chart"
              style={{ width, minWidth: width }}
              width={width}
              height={height}
              viewBox={`0 0 ${width} ${height}`}
              role="img"
              aria-label={tRisk("95% Trigger Bounds by IV Shift")}
            >
            <title>{tRisk("95% Trigger Bounds by IV Shift")}</title>
            <desc>{tRisk("Trigger chart description")}</desc>
            {yTicks.map((tick) => (
              <g key={tick}>
                <line className="chart-grid" x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} />
                <text className="axis-label" x={yAxisLabelX} y={y(tick) + 4} textAnchor="end">{formatNumber(tick)}</text>
              </g>
            ))}
            {IV_SHIFTS.map((shift, index) => {
              const absoluteIv = formatAbsoluteIv(referenceIv, shift);
              return (
                <g key={shift}>
                  <text className="axis-label overview-iv-axis-label" x={x(index)} y={height - 50} textAnchor="middle">
                    <tspan x={x(index)}>{formatIvShift(shift)}</tspan>
                    {absoluteIv ? <tspan x={x(index)} dy="13">({absoluteIv})</tspan> : null}
                  </text>
                </g>
              );
            })}
            <line className="overview-current-index" x1={margin.left} x2={width - margin.right} y1={y(indexPrice)} y2={y(indexPrice)} />
            <text className="overview-current-index-label" x={margin.left + 8} y={y(indexPrice) - 8}>{tRisk("Current BTC Index")} {formatNumber(indexPrice)}</text>
            {series.flatMap((item) => splitBoundarySegments(item.values).map((segment, segmentIndex) => (
              <path
                key={`${item.accountId}-${item.direction}-${segmentIndex}`}
                data-account={item.accountId}
                data-direction={item.direction}
                className={`overview-boundary-line ${item.direction}`}
                style={{ "--series-color": item.color } as React.CSSProperties}
                d={segment.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${x(point.index).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ")}
              />
            )))}
            {series.flatMap((item) => item.values.flatMap((value, index) => value === null ? [] : [(
              <g key={`${item.accountId}-${item.direction}-${index}`} style={{ "--series-color": item.color } as React.CSSProperties}>
                <circle className={`overview-boundary-point ${item.direction}`} cx={x(index)} cy={y(value)} r="4" />
                <title>{item.label} {tRisk(item.direction === "lower" ? "Lower" : "Upper")} {formatIvShift(IV_SHIFTS[index])} {tRisk("vol")}{formatAbsoluteIv(item.referenceIv, IV_SHIFTS[index]) ? ` (${formatAbsoluteIv(item.referenceIv, IV_SHIFTS[index])})` : ""}: {formatNumber(value)}</title>
                {index === firstBoundaryPointIndex(item.values) ? (
                  <text
                    className="overview-boundary-account-label"
                    data-account={item.accountId}
                    data-direction={item.direction}
                    x={x(index) - 10}
                    y={y(value) + 4}
                    textAnchor="end"
                  >
                    {item.label}
                  </text>
                ) : null}
                {shouldShowBoundaryLabel(item.values, index) ? (
                  <text
                    className="overview-boundary-label"
                    x={x(index)}
                    y={y(value) + (item.direction === "upper" ? -10 : 17)}
                    textAnchor="middle"
                  >
                    {formatNumber(value)}
                  </text>
                ) : null}
              </g>
            )]))}
            <text className="axis-title" x={axisTitleX} y={height - 8} textAnchor="middle">{tRisk("IV Shift (vol pts)")}</text>
            <text className="axis-title" transform={`translate(18 ${margin.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">{tRisk("BTC Trigger Price (USDT)")}</text>
            </svg>
          </div>
        </>
      )}
    </article>
  );
}

interface MarginRatioChartProps {
  curve: RiskCurvePoint[];
  triggers: RiskTriggerBoundary[];
  indexPrice: number;
  breakEvenPrices: number[] | null;
  remainingPositionCount: number;
}

function MarginRatioChart({
  curve,
  triggers,
  indexPrice,
  breakEvenPrices,
  remainingPositionCount
}: MarginRatioChartProps) {
  const plotClipId = React.useId().replaceAll(":", "");
  const width = 820;
  const height = 330;
  const margin = { left: 62, right: 24, top: 22, bottom: 48 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const minimum = curve[0]?.indexPrice ?? 0;
  const maximum = curve.at(-1)?.indexPrice ?? 1;
  const finiteRatios = curve.flatMap((point) => (point.marginRatio === null ? [] : [point.marginRatio]));
  const observedMinimum = finiteRatios.length ? Math.min(...finiteRatios) : 0.4;
  const minimumRatio = Math.max(0, Math.min(0.4, Math.floor((observedMinimum - 0.1) * 10) / 10));
  const maximumRatio = 1;
  const x = (price: number) => margin.left + ((price - minimum) / Math.max(1, maximum - minimum)) * plotWidth;
  const y = (ratio: number) => margin.top + (1 - (ratio - minimumRatio) / Math.max(0.01, maximumRatio - minimumRatio)) * plotHeight;
  const boundedY = (ratio: number) => y(Math.max(minimumRatio, Math.min(maximumRatio, ratio)));

  const segments: RiskCurvePoint[][] = [];
  curve.forEach((point) => {
    if (point.marginRatio === null) return void segments.push([]);
    if (segments.length === 0 || segments.at(-1)?.length === 0) segments.push([point]);
    else segments.at(-1)?.push(point);
  });
  const paths = segments.filter((segment) => segment.length > 0);
  const current = nearestCurvePoint(curve, indexPrice);
  const currentX = x(indexPrice);
  const currentRatio = current?.marginRatio ?? null;
  const currentY = currentRatio === null ? null : boundedY(currentRatio);
  const currentRatioAboveMaximum = currentRatio !== null && currentRatio > maximumRatio;
  const currentTextOnLeft = currentX > width - margin.right - 130;
  const xTicks = Array.from({ length: 5 }, (_, index) => minimum + ((maximum - minimum) * index) / 4);
  const yTicks = Array.from(new Set([minimumRatio, 0.6, 0.8, 0.95, maximumRatio]))
    .filter((tick) => tick >= minimumRatio && tick <= maximumRatio)
    .sort((a, b) => a - b);
  const labelLaneLastX = [-Infinity, -Infinity, -Infinity];
  const breakevenLabels = remainingPositionCount === 0 || breakEvenPrices === null
    ? []
    : [...breakEvenPrices]
        .filter((price) => price >= minimum && price <= maximum)
        .sort((left, right) => left - right)
        .map((price) => {
          const priceX = x(price);
          let lane = labelLaneLastX.findIndex((lastX) => priceX - lastX >= 92);
          if (lane === -1) {
            lane = labelLaneLastX.indexOf(Math.min(...labelLaneLastX));
          }
          labelLaneLastX[lane] = priceX;
          const alignLeft = priceX < margin.left + 72;
          const alignRight = priceX > width - margin.right - 72;
          return {
            price,
            priceX,
            labelX: priceX + (alignLeft ? 5 : alignRight ? -5 : 0),
            labelY: margin.top + 33 + lane * 15,
            textAnchor: alignLeft ? "start" as const : alignRight ? "end" as const : "middle" as const
          };
        });

  return (
    <svg className="risk-chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={tRisk("Margin Ratio by BTC Index")}>
      <defs>
        <clipPath id={plotClipId}>
          <rect x={margin.left} y={margin.top} width={plotWidth} height={plotHeight} />
        </clipPath>
      </defs>
      {yTicks.map((tick) => (
        <g key={tick}>
          <line className={tick === 0.95 ? "threshold-line threshold-95" : tick === 0.8 ? "threshold-line threshold-80" : "chart-grid"} x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} />
          <text className="axis-label" x={margin.left - 10} y={y(tick) + 4} textAnchor="end">{formatNumber(tick * 100)}%</text>
        </g>
      ))}
      {xTicks.map((tick) => (
        <g key={tick}>
          <line className="chart-grid" x1={x(tick)} x2={x(tick)} y1={margin.top} y2={height - margin.bottom} />
          <text className="axis-label" x={x(tick)} y={height - 22} textAnchor="middle">{formatNumber(tick)}</text>
        </g>
      ))}
      <line className="current-index-line" x1={currentX} x2={currentX} y1={margin.top} y2={height - margin.bottom} />
      <text
        className="current-index-caption"
        x={currentTextOnLeft ? currentX - 8 : currentX + 8}
        y={margin.top + 13}
        textAnchor={currentTextOnLeft ? "end" : "start"}
      >
        {tRisk("Current BTC Index")} {formatNumber(indexPrice)}
      </text>
      <g className="margin-ratio-plot" clipPath={`url(#${plotClipId})`}>
        {breakevenLabels.map(({ price, priceX }) => (
          <line
            key={`breakeven-line-${price}`}
            className="breakeven-line"
            x1={priceX}
            x2={priceX}
            y1={margin.top}
            y2={height - margin.bottom}
          />
        ))}
        {paths.map((segment, index) => (
          <path
            key={index}
            className="margin-ratio-curve"
            d={segment.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${x(point.indexPrice).toFixed(2)},${y(point.marginRatio ?? 0).toFixed(2)}`).join(" ")}
          />
        ))}
      </g>
      {breakevenLabels.map(({ price, labelX, labelY, textAnchor }) => (
        <text
          key={`breakeven-label-${price}`}
          className="breakeven-label"
          x={labelX}
          y={labelY}
          textAnchor={textAnchor}
        >
          P&amp;L {formatNumber(price)}
        </text>
      ))}
      {triggers.map((trigger, index) => {
        const point = nearestCurvePoint(curve, trigger.price);
        const markerRatio = point?.marginRatio ?? maximumRatio;
        return (
          <g key={`${trigger.price}-${trigger.direction}-${index}`}>
            <circle className="trigger-marker" cx={x(trigger.price)} cy={y(markerRatio)} r="5" />
            <text className="trigger-label" x={x(trigger.price)} y={y(markerRatio) + (trigger.direction === "upper" ? 18 : -11)} textAnchor="middle">
              {tRisk(trigger.direction === "lower" ? "Lower" : "Upper")} {formatNumber(trigger.price)}
            </text>
          </g>
        );
      })}
      {currentY !== null ? (
        <>
          <circle className="current-marker" cx={currentX} cy={currentY} r="6" />
          <text
            className="current-point-label"
            x={currentTextOnLeft ? currentX - 11 : currentX + 11}
            y={currentRatioAboveMaximum ? currentY + 18 : currentY - 9}
            textAnchor={currentTextOnLeft ? "end" : "start"}
          >
            {tRisk("Curve at current index")} {formatRatio(currentRatio)}
          </text>
        </>
      ) : null}
      <text className="axis-title" x={margin.left + plotWidth / 2} y={height - 3} textAnchor="middle">{tRisk("BTC Index (USDT)")}</text>
      <text className="axis-title" transform={`translate(16 ${margin.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">{tRisk("Margin Ratio")}</text>
      <text className="threshold-caption caption-95" x={width - margin.right} y={y(0.95) - 7} textAnchor="end">{tRisk("95% liquidation threshold")}</text>
      <text className="threshold-caption caption-80" x={width - margin.right} y={y(0.8) - 7} textAnchor="end">{tRisk("80% alert")}</text>
    </svg>
  );
}

interface FocusModalProps {
  accountId: string;
  accountLabel: string;
  response: LiquidationRiskEvaluateResponse | null;
  loading: boolean;
  error: string | null;
  preset: ValuationPreset;
  ivShift: number;
  customValue: string;
  opener: HTMLButtonElement | null;
  onPresetChange: (preset: ValuationPreset) => void;
  onIvShiftChange: (value: number) => void;
  onCustomValueChange: (value: string) => void;
  onRefresh: () => void;
  onClose: () => void;
}

function FocusModal({
  accountId,
  accountLabel,
  response,
  loading,
  error,
  preset,
  ivShift,
  customValue,
  opener,
  onPresetChange,
  onIvShiftChange,
  onCustomValueChange,
  onRefresh,
  onClose
}: FocusModalProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const closeRef = React.useRef<HTMLButtonElement>(null);
  const chartScrollRef = React.useRef<HTMLDivElement>(null);
  const chartViewKeyRef = React.useRef<string | null>(null);
  const account = accountFrom(response, accountId);
  const scenario = selectedScenario(account, ivShift);
  const detail = account?.detail?.ivShiftVolPoints === ivShift ? account.detail : null;
  const current = account?.current;
  const indexPrice = response?.market.indexPrice ?? 0;
  const nearest = nearestBoundary(detail?.triggers ?? scenario?.triggers ?? [], indexPrice);
  const scenarioPoint = detail ? nearestCurvePoint(detail.curve, indexPrice) : null;
  const currentRatioPercent = current?.marginRatio === null || current?.marginRatio === undefined
    ? null
    : Math.max(0, Math.min(100, current.marginRatio * 100));
  const issues = mergeIssues(account?.error ? [account.error] : [], scenario?.warnings, detail?.warnings, account?.warnings, response?.warnings);
  const unavailable = account?.status !== "ok" || scenario?.status !== "ok" || !detail;

  React.useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
        )
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      opener?.focus();
    };
  }, [onClose, opener]);

  React.useLayoutEffect(() => {
    const scroll = chartScrollRef.current;
    if (!detail || !scroll || scroll.scrollWidth <= scroll.clientWidth) return;
    const viewKey = `${accountId}|${preset}|${ivShift}|${preset === "custom" ? customValue : ""}`;
    if (chartViewKeyRef.current === viewKey) return;
    chartViewKeyRef.current = viewKey;
    const minimumPrice = detail.curve[0]?.indexPrice ?? indexPrice;
    const maximumPrice = detail.curve.at(-1)?.indexPrice ?? indexPrice;
    const toContentX = (price: number) => {
      const fraction = (price - minimumPrice) / Math.max(1, maximumPrice - minimumPrice);
      const svgX = 62 + Math.max(0, Math.min(1, fraction)) * (820 - 62 - 24);
      return (svgX / 820) * scroll.scrollWidth;
    };
    const currentContentX = toContentX(indexPrice);
    const triggerContentX = nearest ? toContentX(nearest.price) : currentContentX;
    const span = Math.abs(currentContentX - triggerContentX);
    const baseTarget = span <= scroll.clientWidth * 0.72
      ? (currentContentX + triggerContentX) / 2
      : currentContentX;
    const labelOffset = currentContentX > scroll.scrollWidth * 0.78 ? -48 : 48;
    const target = baseTarget + labelOffset;
    scroll.scrollLeft = Math.max(0, Math.min(scroll.scrollWidth - scroll.clientWidth, target - scroll.clientWidth / 2));
  }, [accountId, customValue, detail, indexPrice, ivShift, nearest, preset]);

  return (
    <div className="focus-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div ref={dialogRef} className="focus-modal" role="dialog" aria-modal="true" aria-labelledby="focus-title">
        <header className="focus-header">
          <div>
            <span className="focus-title-line">
              <h2 id="focus-title">{account?.label ?? accountLabel} · {tRisk("Account details")}</h2>
              <span
                className={`live-indicator ${statusLabel(account).toLowerCase()}`}
                role="img"
                aria-label={tRisk("API status", { status: tRisk(statusLabel(account)) })}
                title={tRisk("API status", { status: tRisk(statusLabel(account)) })}
              />
              <span><UpdatedAgo timestamp={current?.sourceTime ?? response?.updatedAt} /></span>
            </span>
          </div>
          <button ref={closeRef} className="close-button" type="button" onClick={onClose} aria-label={tRisk("Close account details")}><X size={22} /></button>
        </header>

        <section className="focus-toolbar" aria-label={tRisk("Scenario controls")}>
          <div className="toolbar-control">
            <span>{tRisk("Valuation time")}</span>
            <div className="segmented-control">
              {(["now", "plus1d", "plus7d", "custom"] as const).map((value) => (
                <button key={value} type="button" className={preset === value ? "active" : ""} onClick={() => onPresetChange(value)}>
                  {{ now: tRisk("Now"), plus1d: "+1d", plus7d: "+7d", custom: tRisk("Custom") }[value]}
                </button>
              ))}
            </div>
          </div>
          {preset === "custom" ? (
            <label className="custom-time-field">
              <span>{tRisk("Custom time")}</span>
              <input type="datetime-local" value={customValue} onChange={(event) => onCustomValueChange(event.target.value)} />
            </label>
          ) : null}
          <label className="iv-shift-field">
            <span>{tRisk("IV shift")}</span>
            <select value={ivShift} onChange={(event) => onIvShiftChange(Number(event.target.value))}>
              {IV_SHIFTS.map((shift) => <option key={shift} value={shift}>{shift >= 0 ? "+" : ""}{shift} {tRisk("vol")}</option>)}
            </select>
          </label>
          <button className="icon-button focus-refresh" type="button" onClick={onRefresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> {tRisk("Refresh account")}
          </button>
        </section>

        {error ? <div className="risk-banner error" role="alert">{error}</div> : null}
        {account?.status === "unreconciled" ? <div className="risk-banner error">{tRisk("Maintenance margin is unreconciled; scenario outputs are unavailable.")}</div> : null}
        {account?.status === "unsupported" ? <div className="risk-banner error">{tRisk("This account configuration is unsupported.")}</div> : null}

        <section className="focus-summary">
          <div><span>{tRisk("BTC Index")}</span><strong>{formatNumber(indexPrice, 2)}</strong></div>
          <div className="focus-ratio-summary">
            <span>{tRisk("Current Margin Ratio")}</span>
            <span className="focus-ratio-content">
              <strong>{formatRatio(current?.marginRatio)}</strong>
              {currentRatioPercent !== null ? (
                <span className="focus-mini-reservoir" style={{ "--risk-level": `${currentRatioPercent}%` } as React.CSSProperties} aria-hidden="true">
                  <span className="focus-mini-fill" />
                  <svg className="focus-mini-ripple" viewBox="0 0 104 8" preserveAspectRatio="none">
                    <path d="M0 4 C5 1 10 1 15 4 S25 7 30 4 S40 1 45 4 S55 7 60 4 S70 1 75 4 S85 7 90 4 S100 1 104 4" />
                  </svg>
                  <span className="focus-mini-threshold threshold-95"><em>95%</em></span>
                  <span className="focus-mini-threshold threshold-80"><em>80%</em></span>
                </span>
              ) : null}
            </span>
          </div>
          <div>
            <span>{nearest ? `${tRisk(nearest.direction === "lower" ? "Lower" : "Upper")} ${tRisk("95% Trigger")}` : tRisk("95% Trigger")}</span>
            <strong className={current?.triggeredNow ? "danger" : ""}>{current?.triggeredNow ? tRisk("Triggered now") : formatNumber(nearest?.price)}</strong>
          </div>
          <div><span>{tRisk("Distance from index")}</span><strong>{current?.triggeredNow ? tRisk("Current") : formatDistance(nearest?.price ?? null, indexPrice)}</strong></div>
          <div><span>{tRisk("Current PNL")}</span><strong className={(current?.unrealizedPnl ?? 0) < 0 ? "danger" : ""}>{formatNumber(current?.unrealizedPnl)}</strong></div>
        </section>

        {loading && !detail ? (
          <section className="focus-unavailable" aria-live="polite">
            <h3>{tRisk("Loading scenario detail…")}</h3>
            <p>{tRisk("The current account result is retained while the selected scenario is calculated.")}</p>
          </section>
        ) : unavailable ? (
          <section className="focus-unavailable">
            <h3>{tRisk("Scenario unavailable")}</h3>
            <p>{issues[0] ? issueMessage(issues[0].code) : tRisk("The selected scenario could not be calculated reliably.")}</p>
          </section>
        ) : (
          <section className="focus-main-grid">
            <article className="focus-panel chart-panel">
              <header><h3>{tRisk("Margin Ratio by BTC Index")}</h3><p>{tRisk("Current markIV · Valuation time", { time: preset === "now" ? tRisk("Now") : new Date(response!.valuationTimeMs).toLocaleString(activeLocale()) })}</p></header>
              <div ref={chartScrollRef} className="risk-chart-scroll">
                <MarginRatioChart
                  curve={detail.curve}
                  triggers={detail.triggers}
                  indexPrice={indexPrice}
                  breakEvenPrices={detail.breakEvenPrices}
                  remainingPositionCount={detail.remainingPositionCount}
                />
              </div>
            </article>
            <aside className="focus-side-stack">
              <article className="focus-panel metric-panel">
                <h3>{tRisk("Margin & equity")}</h3>
                <dl>
                  <div><dt>{tRisk("Adjusted Equity")}</dt><dd>{formatNumber(scenarioPoint?.adjustedEquity)}</dd></div>
                  <div><dt>{tRisk("Maint. Margin")}</dt><dd>{formatNumber(scenarioPoint?.maintenanceMargin)}</dd></div>
                  <div><dt>{tRisk("Derived Wallet")}</dt><dd>{formatNumber(current?.derivedWalletBalance)}</dd></div>
                  <div><dt>{tRisk("Long Option Value")}</dt><dd>{formatNumber(current?.longOptionValue)}</dd></div>
                  <div>
                    <dt>{tRisk("P&L Breakeven")}</dt>
                    <dd className="breakeven-values">
                      {detail.remainingPositionCount === 0
                        ? tRisk("No positions at valuation")
                        : detail.breakEvenPrices === null
                          ? "—"
                          : detail.breakEvenPrices.length === 0
                            ? tRisk("No breakeven in scan range")
                            : detail.breakEvenPrices.map((price) => formatNumber(price)).join(" / ")}
                    </dd>
                  </div>
                </dl>
              </article>
              <article className="focus-panel reconciliation-panel">
                <h3>{tRisk("Reconciliation")}</h3>
                <p className={`reconciliation-state ${account?.reconciliation?.status}`}>{account?.reconciliation?.status === "matched" ? tRisk("Matched with Binance") : account?.reconciliation?.status ?? tRisk("Unavailable")}</p>
                <dl>
                  <div><dt>{tRisk("MM drift")}</dt><dd>{account?.reconciliation?.mmDriftPercent === null ? "—" : `${formatNumber(account?.reconciliation?.mmDriftPercent, 3)}%`}</dd></div>
                  <div><dt>{tRisk("Positions")}</dt><dd>{account?.reconciliation ? `${account.reconciliation.coveredPositions} / ${account.reconciliation.totalPositions}` : "—"}</dd></div>
                  <div><dt>{tRisk("Source skew")}</dt><dd>{account?.reconciliation ? `${formatNumber(account.reconciliation.sourceSkewMs)} ms` : "—"}</dd></div>
                  <div><dt>{tRisk("Margin mode")}</dt><dd>{account?.status === "unsupported" ? tRisk("Unsupported") : tRisk("Classic Options Margin")}</dd></div>
                </dl>
              </article>
            </aside>
          </section>
        )}

        {issues.length > 0 ? (
          <section className="risk-warnings" aria-label={tRisk("Scenario warnings")}>
            {issues.map((issue) => <p key={issue.code}>{issueMessage(issue.code)}</p>)}
          </section>
        ) : null}

        {!unavailable && detail ? (
          <section className="contribution-panel focus-panel">
            <h3>{tRisk("Maintenance margin contribution")}</h3>
            <div className="contribution-scroll">
              <table>
                <colgroup>
                  <col className="contribution-symbol-col" />
                  <col className="contribution-side-col" />
                  <col className="contribution-qty-col" />
                  <col className="contribution-entry-col" />
                  <col className="contribution-value-col" />
                  <col className="contribution-margin-col" />
                  <col className="contribution-share-col" />
                </colgroup>
                <thead><tr><th>{tRisk("Symbol")}</th><th>{tRisk("Side")}</th><th>{tRisk("Qty")}</th><th>{tRisk("Entry Price")}</th><th>{tRisk("Calculated Value")}</th><th>{tRisk("Maint. Margin")}</th><th>{tRisk("MM Share")}</th></tr></thead>
                <tbody>
                  {detail.maintenanceMarginContributions.map((item) => (
                    <tr key={item.symbol}>
                      <td>{item.symbol}</td><td className={item.positionSide === "SHORT" ? "danger" : ""}>{item.positionSide} {item.optionSide}</td>
                      <td>{formatNumber(item.quantity, 4)}</td><td>{formatNumber(item.entryPrice, 2)}</td><td>{formatNumber(item.scenarioOptionPrice, 2)}</td>
                      <td>{formatNumber(item.maintenanceMargin, 2)}</td><td>{formatNumber(item.maintenanceMarginShare * 100, 1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        <footer className="focus-footer"><span>{detail ? tRisk("Showing positions", { count: detail.maintenanceMarginContributions.length }) : tRisk("No contribution detail")}</span><strong>{tRisk("Calculated value · not an execution price")}</strong></footer>
      </div>
    </div>
  );
}

interface AuthenticatedRiskPageProps {
  onAuthenticationRequired: () => void;
  onLogout: () => void;
  logoutLoading: boolean;
  preferDefaultAccount: boolean;
}

function AuthenticatedRiskPage({
  onAuthenticationRequired,
  onLogout,
  logoutLoading,
  preferDefaultAccount
}: AuthenticatedRiskPageProps) {
  const { t } = useTranslation("risk");
  const [registry, setRegistry] = React.useState<LiquidationRiskAccountsResponse | null>(null);
  const [registryError, setRegistryError] = React.useState<string | null>(null);
  const [accountView, setAccountView] = React.useState<AccountViewState>({ ready: false, visibleIds: [], activeId: "all" });
  const [accountMenuOpen, setAccountMenuOpen] = React.useState(false);
  const [overviewSelection, setOverviewSelection] = React.useState<ValuationSelection>(() => ({
    preset: "now",
    targetTimeMs: null,
    customValue: toLocalDateTimeInput(Date.now())
  }));
  const [summaryCache, setSummaryCache] = React.useState<Record<string, LiquidationRiskEvaluateResponse>>({});
  const [summaryErrors, setSummaryErrors] = React.useState<Record<string, string | null>>({});
  const [summaryLoadingKey, setSummaryLoadingKey] = React.useState<string | null>(null);
  const [focusAccountId, setFocusAccountId] = React.useState<string | null>(null);
  const [focusOpener, setFocusOpener] = React.useState<HTMLButtonElement | null>(null);
  const [focusResponse, setFocusResponse] = React.useState<LiquidationRiskEvaluateResponse | null>(null);
  const [focusError, setFocusError] = React.useState<string | null>(null);
  const [focusLoading, setFocusLoading] = React.useState(false);
  const [focusSelection, setFocusSelection] = React.useState<ValuationSelection>(() => ({
    preset: "now",
    targetTimeMs: null,
    customValue: toLocalDateTimeInput(Date.now())
  }));
  const [focusIvShift, setFocusIvShift] = React.useState(0);
  const accountsController = React.useRef<AbortController | null>(null);
  const summaryController = React.useRef<AbortController | null>(null);
  const focusController = React.useRef<AbortController | null>(null);
  const summaryBusy = React.useRef(false);
  const focusBusy = React.useRef(false);
  const summarySequence = React.useRef(0);
  const focusSequence = React.useRef(0);
  const summaryUpdatedAt = React.useRef(new Map<string, number>());
  const focusUpdatedAt = React.useRef(0);
  const accountButtons = React.useRef(new Map<string, HTMLButtonElement>());
  const accountMenuRef = React.useRef<HTMLDivElement>(null);

  const requestedAccountIds = React.useMemo(() => {
    if (!accountView.ready) return [];
    return accountView.activeId === "all" ? accountView.visibleIds : [accountView.activeId];
  }, [accountView]);
  const overviewQuery = React.useMemo<OverviewQuery | null>(() => {
    if (requestedAccountIds.length === 0) return null;
    return {
      key: `${requestedAccountIds.join(",")}|${valuationKey(overviewSelection)}`,
      accountIds: requestedAccountIds,
      selection: overviewSelection
    };
  }, [overviewSelection, requestedAccountIds]);
  const summary = overviewQuery ? summaryCache[overviewQuery.key] ?? null : null;
  const summaryError = overviewQuery ? summaryErrors[overviewQuery.key] ?? null : null;
  const summaryLoading = overviewQuery !== null && summaryLoadingKey === overviewQuery.key;
  const defaultAccountId = registry?.defaultAccountId ?? null;
  const defaultMetadata = registry?.accounts.find((account) => account.id === defaultAccountId) ?? null;

  const accountColors = React.useMemo(() => {
    const colors = new Map<string, string>();
    if (!registry) return colors;
    const orderedIds = [
      registry.defaultAccountId,
      ...registry.accounts.map((account) => account.id).filter((id) => id !== registry.defaultAccountId)
    ];
    orderedIds.forEach((id, index) => colors.set(id, ACCOUNT_SERIES_COLORS[index % ACCOUNT_SERIES_COLORS.length]));
    return colors;
  }, [registry]);
  const hiddenConfiguredAccounts = registry?.accounts.filter(
    (account) => account.status === "configured" && !accountView.visibleIds.includes(account.id)
  ) ?? [];
  const displayedMetadata = requestedAccountIds.flatMap((id) => {
    const metadata = registry?.accounts.find((account) => account.id === id);
    return metadata ? [metadata] : [];
  });
  const displayedAccounts = displayedMetadata.flatMap((metadata) => {
    const account = accountFrom(summary, metadata.id);
    return account ? [account] : [];
  });

  const loadAccounts = React.useCallback(async () => {
    accountsController.current?.abort();
    const controller = new AbortController();
    accountsController.current = controller;
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    try {
      const response = await getRiskAccounts(controller.signal);
      setRegistry(response);
      setAccountView((previous) => initializeAccountView(response, previous, preferDefaultAccount));
      setRegistryError(null);
    } catch (error) {
      if (isAuthenticationRequired(error)) {
        onAuthenticationRequired();
        return;
      }
      if (timedOut) setRegistryError("The risk account request timed out.");
      else if (!(error instanceof DOMException && error.name === "AbortError")) setRegistryError(errorText(error));
    } finally {
      window.clearTimeout(timeout);
    }
  }, [onAuthenticationRequired, preferDefaultAccount]);

  const loadSummary = React.useCallback(async (query: OverviewQuery, forceRefresh = false, replace = true) => {
    if (summaryBusy.current && !replace) return;
    const selectedTime = valuationTime(query.selection);
    if (selectedTime === null) {
      setSummaryErrors((errors) => ({ ...errors, [query.key]: "Choose a valid custom valuation time." }));
      return;
    }
    if (replace) summaryController.current?.abort();
    const controller = new AbortController();
    summaryController.current = controller;
    summaryBusy.current = true;
    const sequence = ++summarySequence.current;
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    setSummaryLoadingKey(query.key);
    const request: LiquidationRiskEvaluateRequest = {
      accountIds: query.accountIds,
      valuationTimeMs: selectedTime,
      ivShiftsVolPoints: [...IV_SHIFTS],
      forceRefresh
    };
    try {
      const response = await evaluateRisk(request, controller.signal);
      if (sequence !== summarySequence.current) return;
      setSummaryCache((cache) => ({ ...cache, [query.key]: response }));
      setSummaryErrors((errors) => ({ ...errors, [query.key]: null }));
      summaryUpdatedAt.current.set(query.key, Date.now());
    } catch (error) {
      if (sequence !== summarySequence.current) return;
      if (isAuthenticationRequired(error)) {
        onAuthenticationRequired();
        return;
      }
      if (timedOut) setSummaryErrors((errors) => ({ ...errors, [query.key]: "The risk evaluation timed out." }));
      else if (!(error instanceof DOMException && error.name === "AbortError")) {
        setSummaryErrors((errors) => ({ ...errors, [query.key]: errorText(error) }));
      }
    } finally {
      window.clearTimeout(timeout);
      if (sequence === summarySequence.current) {
        summaryBusy.current = false;
        setSummaryLoadingKey(null);
      }
    }
  }, [onAuthenticationRequired]);

  const loadFocus = React.useCallback(async (
    accountId: string,
    selection: ValuationSelection,
    ivShift: number,
    forceRefresh = false,
    replace = true
  ) => {
    if (focusBusy.current && !replace) return;
    const selectedTime = valuationTime(selection);
    if (selectedTime === null) {
      setFocusError("Choose a valid custom valuation time.");
      return;
    }
    if (replace) focusController.current?.abort();
    const controller = new AbortController();
    focusController.current = controller;
    focusBusy.current = true;
    const sequence = ++focusSequence.current;
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    setFocusLoading(true);
    const request: LiquidationRiskEvaluateRequest = {
      accountIds: [accountId],
      valuationTimeMs: selectedTime,
      ivShiftsVolPoints: [ivShift],
      detailAccountId: accountId,
      detailIvShiftVolPoints: ivShift,
      forceRefresh
    };
    try {
      const response = await evaluateRisk(request, controller.signal);
      if (sequence !== focusSequence.current) return;
      setFocusResponse(response);
      setFocusError(null);
      focusUpdatedAt.current = Date.now();
    } catch (error) {
      if (sequence !== focusSequence.current) return;
      if (isAuthenticationRequired(error)) {
        onAuthenticationRequired();
        return;
      }
      if (timedOut) setFocusError("The scenario request timed out.");
      else if (!(error instanceof DOMException && error.name === "AbortError")) setFocusError(errorText(error));
    } finally {
      window.clearTimeout(timeout);
      if (sequence === focusSequence.current) {
        focusBusy.current = false;
        setFocusLoading(false);
      }
    }
  }, [onAuthenticationRequired]);

  React.useEffect(() => {
    void loadAccounts();
    return () => accountsController.current?.abort();
  }, [loadAccounts]);

  React.useEffect(() => {
    if (!accountView.ready) return;
    try {
      window.localStorage.setItem(VISIBLE_ACCOUNTS_KEY, JSON.stringify(accountView.visibleIds));
      window.localStorage.setItem(ACTIVE_ACCOUNT_KEY, accountView.activeId);
    } catch {
      // Local preference persistence is best-effort; risk data is never stored.
    }
  }, [accountView]);

  React.useEffect(() => {
    if (overviewQuery) void loadSummary(overviewQuery);
  }, [loadSummary, overviewQuery]);

  React.useEffect(() => {
    if (!accountMenuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) setAccountMenuOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAccountMenuOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [accountMenuOpen]);

  React.useEffect(() => {
    const refreshActive = () => {
      if (document.visibilityState !== "visible") return;
      if (focusAccountId) {
        if (focusSelection.preset === "now") void loadFocus(focusAccountId, focusSelection, focusIvShift, false, false);
      } else if (overviewQuery && overviewSelection.preset === "now") {
        void loadSummary(overviewQuery, false, false);
      }
    };
    const interval = window.setInterval(refreshActive, REFRESH_MS);
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      const updatedAt = focusAccountId
        ? focusUpdatedAt.current
        : overviewQuery ? summaryUpdatedAt.current.get(overviewQuery.key) ?? 0 : 0;
      const isNow = focusAccountId ? focusSelection.preset === "now" : overviewSelection.preset === "now";
      if (isNow && Date.now() - updatedAt >= REFRESH_MS) refreshActive();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [focusAccountId, focusIvShift, focusSelection, loadFocus, loadSummary, overviewQuery, overviewSelection.preset]);

  React.useEffect(() => () => {
    focusController.current?.abort();
    summaryController.current?.abort();
  }, []);

  const addAccount = (accountId: string) => {
    setAccountView((previous) => ({
      ...previous,
      visibleIds: previous.visibleIds.includes(accountId) ? previous.visibleIds : [...previous.visibleIds, accountId]
    }));
    setAccountMenuOpen(false);
  };

  const hideAccount = (accountId: string) => {
    setAccountView((previous) => ({
      ...previous,
      visibleIds: previous.visibleIds.filter((id) => id !== accountId),
      activeId: previous.activeId === accountId ? "all" : previous.activeId
    }));
  };

  const changeOverviewPreset = (preset: ValuationPreset) => {
    setOverviewSelection(makeValuationSelection(preset, overviewSelection.customValue));
  };

  const changeOverviewCustom = (customValue: string) => {
    setOverviewSelection(makeValuationSelection("custom", customValue));
  };

  const openFocus = (accountId: string) => {
    summaryController.current?.abort();
    const inheritedSelection = overviewSelection.preset === "now"
      ? makeValuationSelection("now", overviewSelection.customValue)
      : {
          ...overviewSelection,
          targetTimeMs: summary?.valuationTimeMs ?? overviewSelection.targetTimeMs,
          customValue: overviewSelection.preset === "custom"
            ? overviewSelection.customValue
            : toLocalDateTimeInput(summary?.valuationTimeMs ?? overviewSelection.targetTimeMs ?? Date.now())
        };
    setFocusAccountId(accountId);
    setFocusOpener(accountButtons.current.get(accountId) ?? null);
    setFocusSelection(inheritedSelection);
    setFocusIvShift(0);
    setFocusResponse(summary);
    setFocusError(null);
    focusUpdatedAt.current = overviewQuery ? summaryUpdatedAt.current.get(overviewQuery.key) ?? 0 : 0;
    void loadFocus(accountId, inheritedSelection, 0);
  };

  const closeFocus = React.useCallback(() => {
    setFocusAccountId(null);
    focusController.current?.abort();
    focusBusy.current = false;
    setFocusLoading(false);
    if (overviewQuery && Date.now() - (summaryUpdatedAt.current.get(overviewQuery.key) ?? 0) >= REFRESH_MS) {
      void loadSummary(overviewQuery);
    }
  }, [loadSummary, overviewQuery]);

  const changeFocusPreset = (preset: ValuationPreset) => {
    if (!focusAccountId) return;
    const selection = makeValuationSelection(preset, focusSelection.customValue);
    setFocusSelection(selection);
    void loadFocus(focusAccountId, selection, focusIvShift);
  };

  const changeFocusIvShift = (ivShift: number) => {
    if (!focusAccountId) return;
    setFocusIvShift(ivShift);
    void loadFocus(focusAccountId, focusSelection, ivShift);
  };

  const changeFocusCustom = (customValue: string) => {
    if (!focusAccountId) return;
    const selection = makeValuationSelection("custom", customValue);
    setFocusSelection(selection);
    if (valuationTime(selection) !== null) void loadFocus(focusAccountId, selection, focusIvShift);
  };

  const registryIssue = defaultMetadata && defaultMetadata.status !== "configured"
    ? issueMessage(defaultMetadata.status)
    : null;
  const apiStatus = overviewStatus(summary, requestedAccountIds.length);

  return (
    <ToolkitPageShell
      activePage="risk"
      className="risk-app"
      loading={summaryLoading}
      sessionAction={{ label: t("Sign out"), onClick: onLogout, disabled: logoutLoading }}
      onRefresh={() => {
        void loadAccounts();
        if (overviewQuery) void loadSummary(overviewQuery, true);
      }}
    >

      <section className="risk-overview-controls">
        <div className="overview-account-control">
          <span className="overview-control-label">{t("Accounts")}</span>
          <div className="account-filter-row">
            <div className="account-filter-tabs">
              <button
                type="button"
                className={`account-filter${accountView.activeId === "all" ? " active" : ""}`}
                onClick={() => setAccountView((previous) => ({ ...previous, activeId: "all" }))}
              >
                {t("All accounts")}
              </button>
              {accountView.visibleIds.map((accountId) => {
                const metadata = registry?.accounts.find((account) => account.id === accountId);
                if (!metadata) return null;
                return (
                  <span key={accountId} className={`visible-account-chip${accountView.activeId === accountId ? " active" : ""}`}>
                    <button type="button" onClick={() => setAccountView((previous) => ({ ...previous, activeId: accountId }))}>
                      {metadata.label}
                    </button>
                    <button type="button" className="account-chip-hide" onClick={() => hideAccount(accountId)} aria-label={t("Hide account", { account: metadata.label })}>
                      <X size={13} />
                    </button>
                  </span>
                );
              })}
            </div>
            <div ref={accountMenuRef} className="account-add-control">
              <button
                className="account-add-button"
                type="button"
                aria-label={t("Add visible account")}
                aria-haspopup="menu"
                aria-expanded={accountMenuOpen}
                disabled={hiddenConfiguredAccounts.length === 0}
                onClick={() => setAccountMenuOpen((open) => !open)}
              >
                <Plus size={17} />
              </button>
              {accountMenuOpen ? (
                <div className="account-add-menu" role="menu">
                  <strong>{t("Add account")}</strong>
                  {hiddenConfiguredAccounts.map((account) => (
                    <button key={account.id} type="button" role="menuitem" onClick={() => addAccount(account.id)}>
                      <span>{account.label}</span><small>{account.id}</small>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="overview-valuation-control">
          <span className="overview-control-label">{t("Valuation time")}</span>
          <div className="segmented-control overview-time-buttons" role="group" aria-label={t("Overview valuation time presets")}>
            {(["now", "plus1d", "plus7d", "custom"] as const).map((preset) => (
              <button
                key={preset}
                type="button"
                className={overviewSelection.preset === preset ? "active" : ""}
                aria-pressed={overviewSelection.preset === preset}
                onClick={() => changeOverviewPreset(preset)}
              >
                {{ now: t("Now"), plus1d: "+1d", plus7d: "+7d", custom: t("Custom") }[preset]}
              </button>
            ))}
          </div>
          {overviewSelection.preset === "custom" ? (
            <input
              aria-label={t("Overview custom valuation time")}
              type="datetime-local"
              value={overviewSelection.customValue}
              onChange={(event) => changeOverviewCustom(event.target.value)}
            />
          ) : null}
        </div>

        <div className="overview-market-metric">
          <span>{t("BTC Index (USDT)")}</span>
          <strong>{formatNumber(summary?.market.indexPrice, 2)}</strong>
        </div>
        <div className="risk-api-status overview-api-status">
          <div className="overview-api-heading">
            <span className={`live-indicator ${apiStatus.toLowerCase()}`} />
            <strong>API · {t(apiStatus)}</strong>
          </div>
          <span><UpdatedAgo timestamp={summary?.market.updatedAt ?? summary?.updatedAt} /></span>
        </div>
      </section>

      {registryError || registryIssue ? <div className="risk-banner error" role="alert">{t(registryError ?? registryIssue ?? "")}</div> : null}
      {summaryError && summary ? <div className="risk-banner warning" role="alert">{t("Last successful risk result retained.")} {t(summaryError)}</div> : null}
      {summaryError && !summary ? <div className="risk-banner error" role="alert">{t(summaryError)}</div> : null}

      <section
        className="multi-account-overview"
        data-layout={displayedMetadata.length <= 1 ? "single" : displayedMetadata.length === 2 ? "double" : "multi"}
        aria-label={t("Visible account risk overview")}
      >
        {!registry && !registryError ? <div className="risk-loading-card">{t("Loading account configuration…")}</div> : null}
        {registry && !summary && !summaryError ? <div className="risk-loading-card">{t("Calculating account scenarios…", { count: requestedAccountIds.length })}</div> : null}
        {registry && (summary || summaryError) ? displayedMetadata.map((metadata) => {
          const account = accountFrom(summary, metadata.id);
          return (
            <AccountCard
              key={metadata.id}
              metadataLabel={metadata.label}
              account={account}
              scenario={selectedScenario(account, 0)}
              response={summary}
              loading={summaryLoading}
              error={summary || !summaryError ? null : t(summaryError)}
              onOpen={() => openFocus(metadata.id)}
              buttonRef={(element) => {
                if (element) accountButtons.current.set(metadata.id, element);
                else accountButtons.current.delete(metadata.id);
              }}
            />
          );
        }) : null}
      </section>

      {summary ? (
        <TriggerBoundsChart
          accounts={displayedAccounts}
          accountColors={accountColors}
          indexPrice={summary.market.indexPrice}
          scanMinimum={summary.scan.minimum}
          scanMaximum={summary.scan.maximum}
        />
      ) : null}

      {focusAccountId ? (
        <FocusModal
          accountId={focusAccountId}
          accountLabel={registry?.accounts.find((account) => account.id === focusAccountId)?.label ?? focusAccountId}
          response={focusResponse}
          loading={focusLoading}
          error={focusError ? t(focusError) : null}
          preset={focusSelection.preset}
          ivShift={focusIvShift}
          customValue={focusSelection.customValue}
          opener={focusOpener}
          onPresetChange={changeFocusPreset}
          onIvShiftChange={changeFocusIvShift}
          onCustomValueChange={changeFocusCustom}
          onRefresh={() => void loadFocus(focusAccountId, focusSelection, focusIvShift, true)}
          onClose={closeFocus}
        />
      ) : null}
    </ToolkitPageShell>
  );
}

type RiskAuthView = "checking" | "anonymous" | "authenticated" | "unavailable";

export function RiskPage() {
  const { t } = useTranslation("risk");
  const [view, setView] = React.useState<RiskAuthView>("checking");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loginLoading, setLoginLoading] = React.useState(false);
  const [logoutLoading, setLogoutLoading] = React.useState(false);
  const [preferDefaultAccount, setPreferDefaultAccount] = React.useState(false);
  const sessionController = React.useRef<AbortController | null>(null);

  const checkSession = React.useCallback(async () => {
    sessionController.current?.abort();
    const controller = new AbortController();
    sessionController.current = controller;
    setView("checking");
    setError(null);
    try {
      const session = await getRiskAuthSession(controller.signal);
      setPreferDefaultAccount(false);
      setView(session.authenticated ? "authenticated" : "anonymous");
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Risk authentication is temporarily unavailable.");
      setView("unavailable");
    }
  }, []);

  React.useEffect(() => {
    void checkSession();
    return () => sessionController.current?.abort();
  }, [checkSession]);

  const submitLogin = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loginLoading) return;
    const controller = new AbortController();
    setLoginLoading(true);
    setError(null);
    try {
      const session = await loginRisk(password, controller.signal);
      if (!session.authenticated) throw new Error(t("Risk authentication did not complete."));
      setPassword("");
      setPreferDefaultAccount(true);
      setView("authenticated");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t("Risk login failed."));
      setView("anonymous");
    } finally {
      setLoginLoading(false);
    }
  };

  const handleAuthenticationRequired = React.useCallback(() => {
    setPreferDefaultAccount(false);
    setError("Your Risk session ended. Enter the access password again.");
    setView("anonymous");
  }, []);

  const handleLogout = async () => {
    if (logoutLoading) return;
    const controller = new AbortController();
    setLogoutLoading(true);
    setError(null);
    try {
      await logoutRisk(controller.signal);
      setPassword("");
      setPreferDefaultAccount(false);
      setView("anonymous");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t("Risk logout failed."));
    } finally {
      setLogoutLoading(false);
    }
  };

  if (view === "authenticated") {
    return (
      <AuthenticatedRiskPage
        onAuthenticationRequired={handleAuthenticationRequired}
        onLogout={() => void handleLogout()}
        logoutLoading={logoutLoading}
        preferDefaultAccount={preferDefaultAccount}
      />
    );
  }

  return (
    <ToolkitPageShell
      activePage="risk"
      className="risk-app risk-auth-shell"
      loading={view === "checking" || loginLoading}
      onRefresh={() => void checkSession()}
    >
      <section className="risk-login-stage" aria-live="polite">
        <div className="risk-login-card">
          <span className="risk-login-icon" aria-hidden="true"><LockKeyhole size={24} /></span>
          <div className="risk-login-copy">
            <p className="eyebrow">{t("Private account data")}</p>
            <h2>{t("Unlock Liquidation Risk")}</h2>
            <p>{t("Sign in once on this device. Access remains available until you sign out, revoke the session, or the browser removes its cookie.")}</p>
          </div>
          {view === "checking" ? <p className="risk-login-status">{t("Checking Risk access…")}</p> : null}
          {view !== "checking" ? (
            <form className="risk-login-form" onSubmit={(event) => void submitLogin(event)}>
              <label htmlFor="risk-access-password">{t("Access password")}</label>
              <input
                id="risk-access-password"
                type="password"
                value={password}
                minLength={12}
                maxLength={256}
                autoComplete="current-password"
                autoFocus={view === "anonymous"}
                onChange={(event) => setPassword(event.target.value)}
                disabled={loginLoading || view === "unavailable"}
                required
              />
              <button type="submit" disabled={loginLoading || view === "unavailable"}>
                {loginLoading ? t("Unlocking…") : t("Unlock")}
              </button>
            </form>
          ) : null}
          {error ? <div className="risk-login-error" role="alert">{t(error)}</div> : null}
          {view === "unavailable" ? (
            <button type="button" className="risk-login-retry" onClick={() => void checkSession()}>
              {t("Try again")}
            </button>
          ) : null}
        </div>
      </section>
    </ToolkitPageShell>
  );
}
