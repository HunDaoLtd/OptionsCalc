import React from "react";
import ReactDOM from "react-dom/client";
import { useTranslation } from "react-i18next";
import i18n, { getNumberFormatter } from "./i18n";
import { buildMatrix, filterStrikesByInterval, selectNearbyStrikes } from "./lib/options";
import { RiskPage } from "./risk-page";
import { ToolkitPageShell, UpdatedAgo } from "./toolkit";
import type { OptionSnapshot, YieldMode } from "./types";
import "./styles.css";

const REFRESH_MS = 30_000;
const REQUEST_TIMEOUT_MS = 15_000;
const API_BASE = import.meta.env.BASE_URL;
const MARGIN_STORAGE_KEY = "btc-put-yield-margin";
const STRIKE_COUNT_STORAGE_KEY = "btc-put-yield-strikeCount";
const STRIKE_INTERVAL_STORAGE_KEY = "btc-put-yield-strikeInterval";

type SelectedCell = { expiry: number; strike: number };

function formatNumber(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return getNumberFormatter(digits).format(value);
}

function formatMatrixNumber(value: number | null | undefined) {
  return formatNumber(value, 1);
}

function formatMatrixPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return `${formatMatrixNumber(value)}%`;
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function formatIV(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return formatMatrixNumber(value * 100);
}

function getExpiryParts(label: string) {
  const [date = label, time = ""] = label.split(" ");
  return { date, time };
}

function formatExpiryDuration(days: number | null | undefined) {
  if (days === null || days === undefined || !Number.isFinite(days)) return "-";

  const totalMinutes = Math.max(0, Math.floor(days * 24 * 60));
  const wholeDays = Math.floor(totalMinutes / (24 * 60));
  const hours = Math.floor(totalMinutes / 60) % 24;
  if (wholeDays <= 0) return `${hours}h:${String(totalMinutes % 60).padStart(2, '0')}m`;
  return `${wholeDays}d:${hours}h`;
}

function shouldShowMark(
  cell: { premium: number; netPremium: number; contract: { strike: number } | null },
  indexPrice: number | null | undefined
) {
  if (indexPrice === null || indexPrice === undefined || !Number.isFinite(indexPrice)) return false;
  if (!cell.contract || cell.contract.strike <= indexPrice) return false;
  return formatMatrixNumber(cell.premium) !== formatMatrixNumber(cell.netPremium);
}

function formatYieldError(error: string) {
  const knownMessages = ["Empty API response", "Load failed", "Load timed out"];
  if (knownMessages.includes(error)) return String(i18n.t(error, { ns: "yield" }));
  return String(i18n.t("Market data error", { ns: "yield", detail: error }));
}

function useOptionSnapshot() {
  const [snapshot, setSnapshot] = React.useState<OptionSnapshot | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);

  const load = React.useCallback(async () => {
    setLoading(true);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${API_BASE}api/options/btc-put`, { signal: controller.signal });
      const text = await response.text();
      const data = text ? JSON.parse(text) : null;
      if (!data) throw new Error("Empty API response");
      if (!response.ok) throw new Error(data.error ?? "Load failed");
      setSnapshot(data);
      setError(data.sourceError ?? null);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") {
        setError("Load timed out");
      } else {
        setError(requestError instanceof Error ? requestError.message : "Load failed");
      }
    } finally {
      window.clearTimeout(timeoutId);
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  return { snapshot, error, loading, reload: load };
}

export function YieldPage() {
  const { t } = useTranslation("yield");
  const { snapshot, error, loading, reload } = useOptionSnapshot();
  const [margin, setMargin] = React.useState(() => {
    const saved = window.localStorage.getItem(MARGIN_STORAGE_KEY);
    const parsed = saved ? Number(saved) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 10000;
  });
  const [referencePrice, setReferencePrice] = React.useState<number | "">("");
  const [strikeCount, setStrikeCount] = React.useState(() => {
    const saved = window.localStorage.getItem(STRIKE_COUNT_STORAGE_KEY);
    const parsed = saved ? Number(saved) : NaN;
    return Number.isFinite(parsed) && parsed >= 1 && parsed <= 12 ? parsed : 6;
  });
  const [strikeInterval, setStrikeInterval] = React.useState(() => {
    const saved = window.localStorage.getItem(STRIKE_INTERVAL_STORAGE_KEY);
    const parsed = saved ? Number(saved) : NaN;
    return [500, 1000, 2500, 5000].includes(parsed) ? parsed : 1000;
  });
  const [yieldMode, setYieldMode] = React.useState<YieldMode>("actual");
  const [lockedStrikes, setLockedStrikes] = React.useState<number[]>([]);
  const [selectedCell, setSelectedCell] = React.useState<SelectedCell | null>(null);
  React.useEffect(() => {
    if (referencePrice === "" && snapshot?.indexPrice) {
      setReferencePrice(Math.round(snapshot.indexPrice / 1000) * 1000);
    }
  }, [referencePrice, snapshot?.indexPrice]);

  React.useEffect(() => {
    window.localStorage.setItem(MARGIN_STORAGE_KEY, String(margin));
  }, [margin]);

  React.useEffect(() => {
    window.localStorage.setItem(STRIKE_COUNT_STORAGE_KEY, String(strikeCount));
  }, [strikeCount]);

  React.useEffect(() => {
    window.localStorage.setItem(STRIKE_INTERVAL_STORAGE_KEY, String(strikeInterval));
  }, [strikeInterval]);

  const strikes = React.useMemo(
    () => Array.from(new Set((snapshot?.contracts ?? []).map((contract) => contract.strike))),
    [snapshot]
  );

  const selectedStrikes = React.useMemo(() => {
    const intervalStrikes = filterStrikesByInterval(strikes, strikeInterval);
    const auto = selectNearbyStrikes(intervalStrikes, Number(referencePrice), strikeCount);
    return Array.from(new Set([...auto, ...lockedStrikes])).sort((a, b) => b - a);
  }, [lockedStrikes, referencePrice, strikeCount, strikeInterval, strikes]);

  const rows = React.useMemo(
    () => buildMatrix(snapshot?.contracts ?? [], selectedStrikes, margin, snapshot?.indexPrice),
    [margin, selectedStrikes, snapshot]
  );

  const indexDividerStrike = React.useMemo(() => {
    const indexPrice = snapshot?.indexPrice;
    if (indexPrice === null || indexPrice === undefined || !Number.isFinite(indexPrice)) return null;
    // selectedStrikes is sorted descending; find the first strike <= indexPrice
    return selectedStrikes.find((strike) => strike <= indexPrice) ?? null;
  }, [selectedStrikes, snapshot?.indexPrice]);

  React.useEffect(() => {
    if (
      selectedCell &&
      !rows.some((row) => row.expiry === selectedCell.expiry && row.cells[selectedCell.strike])
    ) {
      setSelectedCell(null);
    }
  }, [rows, selectedCell]);

  const visibleYields = rows.flatMap((row) =>
    selectedStrikes.map((strike) =>
      yieldMode === "actual" ? row.cells[strike]?.actualYield : row.cells[strike]?.cashSecuredYield
    )
  );
  const maxAbsYield = Math.max(1, ...visibleYields.filter(isFiniteNumber).map((value) => Math.abs(value)));

  return (
    <ToolkitPageShell activePage="yield" loading={loading} onRefresh={() => void reload()}>

      <section className="controls">
        <div className="control-grid">
          <label className="control-field">
            <span>{t("Reference")}</span>
            <input
              type="number"
              value={referencePrice}
              onChange={(event) => setReferencePrice(event.target.value === "" ? "" : Number(event.target.value))}
              min="0"
              step="500"
            />
          </label>
          <label className="control-field">
            <span>{t("Margin / Contract")}</span>
            <input
              type="number"
              value={margin}
              onChange={(event) => setMargin(Math.max(1, Number(event.target.value)))}
              min="0"
              step="100"
              disabled={yieldMode === "cashSecured"}
            />
          </label>
          <label className="control-field">
            <span>{t("Nearby Strikes")}</span>
            <input
              type="number"
              value={strikeCount}
              onChange={(event) => setStrikeCount(Math.max(1, Math.min(12, Number(event.target.value))))}
              min="1"
              max="12"
            />
          </label>
          <label className="control-field">
            <span>{t("Strike Interval")}</span>
            <select value={strikeInterval} onChange={(event) => setStrikeInterval(Number(event.target.value))}>
              <option value="500">500</option>
              <option value="1000">1000</option>
              <option value="2500">2500</option>
              <option value="5000">5000</option>
            </select>
          </label>
          <label className="control-field index-field">
            <span>{t("BTC Index (USDT)")}</span>
            <output className="index-output">{formatNumber(snapshot?.indexPrice, 2)}</output>
          </label>
          <div className="control-field yield-mode-field">
            <span>{t("Yield Mode")}</span>
            <div className="mode-toggle" role="group" aria-label={t("Yield mode")}>
              <button className={yieldMode === "actual" ? "active" : ""} onClick={() => setYieldMode("actual")}>
                {t("Actual")}
              </button>
              <button className={yieldMode === "cashSecured" ? "active" : ""} onClick={() => setYieldMode("cashSecured")}>
                {t("Secured")}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="status-line">
        <span><UpdatedAgo timestamp={snapshot?.updatedAt} showAbsoluteTime /></span>
        {snapshot?.stale ? <strong>{t("Showing last successful snapshot")}</strong> : null}
        {error ? <strong>{formatYieldError(error)}</strong> : null}
      </section>

      <section className="matrix-wrap">
        <table>
          <thead>
            <tr>
              <th className="expiry-col">{t("Expiry")}</th>
              <th>{t("Days")}</th>
              {selectedStrikes.map((strike) => (
                <th key={strike} className={strike === indexDividerStrike ? "index-divider" : undefined}>
                  <button
                    className={lockedStrikes.includes(strike) ? "strike locked" : "strike"}
                    onClick={() =>
                      setLockedStrikes((current) =>
                        current.includes(strike) ? current.filter((item) => item !== strike) : [...current, strike]
                      )
                    }
                    title={t("Lock or unlock this strike")}
                  >
                    {strike}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.expiry}>
                <th className="expiry-col row-title">
                  <span>{getExpiryParts(row.expiryLabel).date}</span>
                  <small>{formatExpiryDuration(row.daysToExpiry)}</small>
                </th>
                <td className="days">{formatMatrixNumber(row.daysToExpiry)}</td>
                {selectedStrikes.map((strike) => {
                  const cell = row.cells[strike];
                  const yieldValue = yieldMode === "actual" ? cell?.actualYield : cell?.cashSecuredYield;
                  const finiteYield = isFiniteNumber(yieldValue) ? yieldValue : null;
                  const barWidth = finiteYield === null ? 0 : Math.min(100, (Math.abs(finiteYield) / maxAbsYield) * 100);
                  const yieldTone = finiteYield === null || finiteYield === 0 ? "" : finiteYield < 0 ? " negative" : " positive";
                  const isSelected = selectedCell?.expiry === row.expiry && selectedCell.strike === strike;

                  return (
                    <td
                      key={`${row.expiry}-${strike}`}
                      className={`yield-cell${cell ? " selectable" : ""}${yieldTone}${isSelected ? " selected" : ""}${strike === indexDividerStrike ? " index-divider" : ""}`}
                      onClick={cell ? () => setSelectedCell({ expiry: row.expiry, strike }) : undefined}
                      onKeyDown={
                        cell
                          ? (event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                setSelectedCell({ expiry: row.expiry, strike });
                              }
                            }
                          : undefined
                      }
                      role={cell ? "button" : undefined}
                      tabIndex={cell ? 0 : undefined}
                      aria-pressed={cell ? isSelected : undefined}
                      aria-label={cell ? t("expiry strike label", { expiry: row.expiryLabel, strike }) : undefined}
                    >
                      {cell ? (
                        <>
                          <div className="bar" style={{ width: `${barWidth}%` }} />
                          <div className="cell-content">
                            <span className="iv-value">{formatIV(cell.contract?.markIV)}</span>
                            <strong>{formatMatrixPercent(yieldValue)}</strong>
                            <span className="net-premium">{formatMatrixNumber(cell.netPremium)}</span>
                            {shouldShowMark(cell, snapshot?.indexPrice) ? (
                              <span className="mark-price">{formatMatrixNumber(cell.premium)}</span>
                            ) : null}
                          </div>
                        </>
                      ) : (
                        <span className="empty">-</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && rows.length === 0 ? <div className="empty-state">{t("No BTC Put contracts to display")}</div> : null}
      </section>
    </ToolkitPageShell>
  );
}

const root = document.getElementById("root");
if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      {window.location.pathname.startsWith(`${API_BASE}risk`) ? <RiskPage /> : <YieldPage />}
    </React.StrictMode>
  );
}
