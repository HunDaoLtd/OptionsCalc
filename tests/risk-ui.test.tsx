/** @format */
// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n, { syncDocumentLanguage } from "../src/i18n";
import { evaluateRisk, getRiskAccounts, issueMessage, parseRiskAccountsResponse, parseRiskEvaluateResponse, RiskApiClientError } from "../src/risk-api";
import { RiskPage } from "../src/risk-page";
import { RISK_ISSUE_CODES } from "../src/types";
import type { LiquidationRiskEvaluateResponse } from "../src/types";

beforeEach(async () => {
  await i18n.changeLanguage("en");
  syncDocumentLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
});

const ACCOUNTS = {
  defaultAccountId: "main",
  accounts: [
    { id: "main", label: "Main", status: "configured" as const },
    { id: "income", label: "Income", status: "configured" as const },
    { id: "hedge", label: "Hedge", status: "configured" as const },
    { id: "broken", label: "Broken", status: "missing_credentials" as const }
  ]
};

function evaluation(
  ivShift = 0,
  status: "ok" | "unreconciled" = "ok",
  accountIds: string[] = ["main"],
  ivShifts: number[] = [ivShift],
  detailAccountId: string | null = "main"
): LiquidationRiskEvaluateResponse {
  const unavailable = status === "unreconciled";
  return {
    updatedAt: 1_785_542_401_000,
    market: { indexPrice: 62_000, updatedAt: 1_785_542_400_000, stale: false },
    valuationTimeMs: 1_785_542_400_000,
    scan: { minimum: 12_400, maximum: 124_000, sampleCount: 2001, boundaryTolerance: 1 },
    accounts: accountIds.map((accountId, accountIndex) => {
      const metadata = ACCOUNTS.accounts.find((account) => account.id === accountId)!;
      const offset = accountIndex * 2_400;
      return {
        id: accountId,
        label: metadata.label,
        status: accountId === "main" ? status : "ok",
        referenceIv: 0.337 + accountIndex * 0.05,
        error: unavailable ? { code: "unreconciled_maintenance_margin" } : null,
        current: {
          adjustedEquity: 184_320 + offset,
          maintenanceMargin: 134_210 - offset,
          marginRatio: 0.728 - accountIndex * 0.1,
          derivedWalletBalance: 181_060,
          longOptionValue: 3_260,
          unrealizedPnl: -6_740,
          sourceTime: 1_785_542_400_200 - accountIndex * 5_000,
          stale: false,
          triggeredNow: false,
          triggerReason: null
        },
        reconciliation: {
          status: unavailable ? "unreconciled" : "matched",
          mmDriftPercent: unavailable ? 2.4 : 0.03,
          coveredPositions: 3,
          totalPositions: 3,
          sourceSkewMs: 700
        },
        scenarios: unavailable && accountId === "main"
          ? null
          : ivShifts.map((shift, shiftIndex) => ({
                ivShiftVolPoints: shift,
                status: "ok",
                lowerTrigger: accountId === "hedge" && shift === 10 ? null : 51_240 + offset + shiftIndex * 800,
                upperTrigger: accountId === "income" && shift === 20 ? null : 82_500 + offset + shiftIndex * 900,
                triggers: [
                  { price: 51_240 + offset + shiftIndex * 800, direction: "lower", reason: "margin_ratio_threshold" },
                  { price: 82_500 + offset + shiftIndex * 900, direction: "upper", reason: "margin_ratio_threshold" }
                ],
                warnings: []
              })),
        detail: unavailable || detailAccountId !== accountId
          ? null
          : {
              ivShiftVolPoints: ivShift,
              breakEvenPrices: [51_240, 82_500],
              remainingPositionCount: 3,
              curve: [
                { indexPrice: 40_000, adjustedEquity: 120_000, maintenanceMargin: 130_000, marginRatio: 1.083, triggered: true, triggerReason: "margin_ratio_threshold" },
                { indexPrice: 62_000, adjustedEquity: 184_320, maintenanceMargin: 134_210, marginRatio: 0.728, triggered: false, triggerReason: null },
                { indexPrice: 90_000, adjustedEquity: 110_000, maintenanceMargin: 120_000, marginRatio: null, triggered: true, triggerReason: "non_positive_adjusted_equity" }
              ],
              maintenanceMarginContributions: [
                { symbol: "BTC-260925-60000-P", optionSide: "PUT", positionSide: "SHORT", quantity: -1.2, entryPrice: 1750.25, scenarioOptionPrice: 2418.6, maintenanceMargin: 61_840, maintenanceMarginShare: 0.461, expiryMs: 1_790_000_000_000 }
              ],
              triggers: [
                { price: 51_240, direction: "lower", reason: "margin_ratio_threshold" },
                { price: 82_500, direction: "upper", reason: "non_positive_adjusted_equity" }
              ],
              warnings: []
            },
        warnings: []
      } as LiquidationRiskEvaluateResponse["accounts"][number];
    }),
    warnings: [{ code: "open_orders_not_included" }]
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch(
  status: "ok" | "unreconciled" = "ok",
  decorate?: (response: LiquidationRiskEvaluateResponse) => LiquidationRiskEvaluateResponse
) {
  const requests: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null;
    requests.push({ url, body });
    if (url.endsWith("/auth/session")) return jsonResponse({ authenticated: true });
    if (url.endsWith("/accounts")) return jsonResponse(ACCOUNTS);
    const shifts = (body?.ivShiftsVolPoints as number[] | undefined) ?? [0];
    const shift = Number((body?.detailIvShiftVolPoints as number | undefined) ?? shifts[0] ?? 0);
    const accountIds = (body?.accountIds as string[] | undefined) ?? ["main"];
    const detailAccountId = typeof body?.detailAccountId === "string" ? body.detailAccountId : null;
    const response = evaluation(shift, status, accountIds, shifts, detailAccountId);
    if (typeof body?.valuationTimeMs === "number") response.valuationTimeMs = body.valuationTimeMs;
    return jsonResponse(decorate ? decorate(response) : response);
  });
  vi.stubGlobal("fetch", mock);
  return { mock, requests };
}

describe("risk runtime contract", () => {
  it("accepts frozen responses and rejects malformed numeric fields", () => {
    expect(parseRiskAccountsResponse(ACCOUNTS).defaultAccountId).toBe("main");
    expect(parseRiskEvaluateResponse(evaluation()).accounts[0].status).toBe("ok");
    const malformed = evaluation();
    malformed.market.indexPrice = Number.NaN;
    expect(() => parseRiskEvaluateResponse(malformed)).toThrow(RiskApiClientError);
    const unavailableBreakeven = evaluation();
    unavailableBreakeven.accounts[0].detail!.breakEvenPrices = null;
    expect(parseRiskEvaluateResponse(unavailableBreakeven).accounts[0].detail?.breakEvenPrices).toBeNull();
    const malformedBreakeven = evaluation();
    malformedBreakeven.accounts[0].detail!.breakEvenPrices = [Number.NaN];
    expect(() => parseRiskEvaluateResponse(malformedBreakeven)).toThrow(RiskApiClientError);
    const negativePositionCount = evaluation();
    negativePositionCount.accounts[0].detail!.remainingPositionCount = -1;
    expect(() => parseRiskEvaluateResponse(negativePositionCount)).toThrow(RiskApiClientError);
    const fractionalPositionCount = evaluation();
    fractionalPositionCount.accounts[0].detail!.remainingPositionCount = 1.5;
    expect(() => parseRiskEvaluateResponse(fractionalPositionCount)).toThrow(RiskApiClientError);
    const missingPositionCount = evaluation();
    Reflect.deleteProperty(missingPositionCount.accounts[0].detail!, "remainingPositionCount");
    expect(() => parseRiskEvaluateResponse(missingPositionCount)).toThrow(RiskApiClientError);
  });

  it("handles a non-JSON gateway error and maps every frozen issue code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad gateway", { status: 502 })));
    await expect(getRiskAccounts(new AbortController().signal)).rejects.toMatchObject({ code: "invalid_response", status: 502 });
    expect(RISK_ISSUE_CODES.every((code) => issueMessage(code).length > 10)).toBe(true);
  });

  it("preserves typed 400 and 503 API errors", async () => {
    const request = { accountIds: ["main"], valuationTimeMs: Date.now(), ivShiftsVolPoints: [0] };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: { code: "invalid_request", issues: [{ code: "invalid_valuation_time" }] } }, 400)));
    await expect(evaluateRisk(request, new AbortController().signal)).rejects.toMatchObject({ code: "invalid_request", status: 400 });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: { code: "service_unavailable", issues: [{ code: "upstream_unavailable" }] } }, 503)));
    await expect(evaluateRisk(request, new AbortController().signal)).rejects.toMatchObject({ code: "service_unavailable", status: 503 });
  });
});

describe("risk overview and Focus flow", () => {
  it("switches cards, charts, Focus content, issues, and accessibility text to Chinese", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);

    const originalCard = await screen.findByRole("button", { name: "Open Main risk details" });
    await waitFor(() => expect(originalCard.textContent).toContain("Lower Trigger"));
    const requestCount = requests.length;
    await user.click(screen.getByRole("button", { name: "Chinese" }));

    const card = await screen.findByRole("button", { name: "打开 Main 的风险详情" });
    await waitFor(() => expect(card.textContent).toContain("下方触发价"));
    expect(card).toBe(originalCard);
    expect(requests).toHaveLength(requestCount);
    expect(card.textContent).toContain("上方触发价");
    expect(card.querySelector(".live-indicator")?.getAttribute("aria-label")).toBe("API 状态：实时");
    expect(screen.getByRole("img", { name: "不同 IV 偏移下的 95% 强平阈值" })).not.toBeNull();

    await user.click(card);
    expect(await screen.findByRole("dialog", { name: /账户详情/ })).not.toBeNull();
    expect(screen.getByText("保证金与权益")).not.toBeNull();
    expect(screen.getByText("本次估算不包含未成交订单。")).not.toBeNull();
    expect(screen.getByRole("button", { name: "关闭账户详情" })).not.toBeNull();
  });

  it("initializes with only the default account and requests the full overview IV range without detail", async () => {
    const { requests } = installFetch();
    render(<RiskPage />);

    const card = await screen.findByRole("button", { name: "Open Main risk details" });
    await waitFor(() => expect(card.textContent).toContain("Lower Trigger"));
    expect(card.textContent).toContain("Upper Trigger");
    expect(card.querySelector(".risk-ripple")).not.toBeNull();
    expect(card.textContent).not.toContain("API · LIVE");
    expect(card.querySelector(".live-indicator.live")?.getAttribute("aria-label")).toBe("API status: LIVE");
    expect(document.querySelector(".multi-account-overview")?.getAttribute("data-layout")).toBe("single");
    await waitFor(() => expect(requests.filter((item) => item.url.endsWith("/evaluate"))).toHaveLength(1));
    expect(requests.find((item) => item.url.endsWith("/evaluate"))?.body).toMatchObject({
      accountIds: ["main"],
      ivShiftsVolPoints: [-50, -40, -30, -20, -10, 0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150],
      forceRefresh: false
    });
    expect(requests.find((item) => item.url.endsWith("/evaluate"))?.body).not.toHaveProperty("detailAccountId");
    expect(await screen.findByText("(53.7%)")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "95% Trigger Bounds" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Reference IV details" })).not.toBeNull();
    expect(screen.getByRole("tooltip").textContent).toContain("Reference IV: position-weighted markIV");
    expect(document.querySelector(".overview-chart-header p")).toBeNull();
    expect(screen.queryByText("Position-weighted markIV + parallel shift · Lower solid · Upper dashed")).toBeNull();
    expect(screen.getByLabelText("Lower trigger, solid line")).not.toBeNull();
    expect(screen.getByLabelText("Upper trigger, dashed line")).not.toBeNull();
    expect(document.querySelector(".overview-chart-footer")).toBeNull();
    expect(window.localStorage.getItem("btc-options-risk-visible-accounts-v1")).toBe('["main"]');
  });

  it("falls back to the default account when stored visible accounts no longer exist", async () => {
    window.localStorage.setItem("btc-options-risk-visible-accounts-v1", JSON.stringify(["retired-account"]));
    const { requests } = installFetch();
    render(<RiskPage />);

    expect(await screen.findByRole("button", { name: "Open Main risk details" })).not.toBeNull();
    await waitFor(() => expect(requests.find((item) => item.url.endsWith("/evaluate"))?.body).toMatchObject({
      accountIds: ["main"]
    }));
    expect(window.localStorage.getItem("btc-options-risk-visible-accounts-v1")).toBe('["main"]');
  });

  it("opens Focus, changes IV, force refreshes, closes with Escape and returns focus", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);
    const card = await screen.findByRole("button", { name: "Open Main risk details" });
    await user.click(card);
    const dialog = await screen.findByRole("dialog", { name: "Main · Account details" });
    expect(await within(dialog).findByText("Margin Ratio by BTC Index")).not.toBeNull();
    const marginRatioChart = within(dialog).getByRole("img", { name: "Margin Ratio by BTC Index" });
    expect(within(marginRatioChart).getByText("100%")).not.toBeNull();
    expect(within(marginRatioChart).queryByText("110%")).toBeNull();
    expect(within(marginRatioChart).queryByText("200%")).toBeNull();
    expect(marginRatioChart.querySelector(".margin-ratio-plot")?.getAttribute("clip-path")).toMatch(/^url\(#.+\)$/);
    expect(marginRatioChart.querySelector(".margin-ratio-curve")?.getAttribute("d")).not.toContain("M62.00,22.00");
    expect(within(dialog).queryByText(/API ·/)).toBeNull();
    expect(within(dialog).getByText("Entry Price")).not.toBeNull();
    expect(within(dialog).getByText("1,750.25")).not.toBeNull();
    expect(within(dialog).getByText("Calculated Value")).not.toBeNull();
    expect(within(dialog).getByText("P&L Breakeven").nextElementSibling?.textContent).toBe("51,240 / 82,500");
    expect(marginRatioChart.querySelectorAll(".breakeven-line")).toHaveLength(2);
    expect(within(marginRatioChart).getByText("P&L 51,240")).not.toBeNull();
    expect(within(marginRatioChart).getByText("P&L 82,500")).not.toBeNull();
    expect(within(dialog).queryByText("Unrealized PNL")).toBeNull();
    expect(dialog.querySelector(".focus-mini-reservoir")).not.toBeNull();
    expect(within(dialog).getByText("Current PNL").nextElementSibling?.textContent).toBe("-6,740");
    expect(within(dialog).getByText("Current PNL").nextElementSibling?.classList.contains("danger")).toBe(true);
    expect(within(dialog).getByText("Current BTC Index 62,000")).not.toBeNull();
    expect(within(dialog).getByText("Curve at current index 72.8%")).not.toBeNull();

    await user.selectOptions(within(dialog).getByLabelText("IV shift"), "20");
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ ivShiftsVolPoints: [20], detailIvShiftVolPoints: 20 }));
    await user.click(within(dialog).getByRole("button", { name: "Refresh account" }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ forceRefresh: true, detailIvShiftVolPoints: 20 }));

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(card);
  });

  it("labels a scenario with no account PNL breakeven in the scan range", async () => {
    installFetch("ok", (response) => {
      response.accounts[0].detail!.breakEvenPrices = [];
      return response;
    });
    const user = userEvent.setup();
    render(<RiskPage />);
    await user.click(await screen.findByRole("button", { name: "Open Main risk details" }));
    const dialog = await screen.findByRole("dialog", { name: "Main · Account details" });
    expect(within(dialog).getByText("P&L Breakeven").nextElementSibling?.textContent).toBe("None in scan range");
    expect(within(dialog).getByRole("img", { name: "Margin Ratio by BTC Index" }).querySelector(".breakeven-line")).toBeNull();
  });

  it("labels an all-expired valuation as having no positions and omits breakeven lines", async () => {
    installFetch("ok", (response) => {
      response.accounts[0].detail!.remainingPositionCount = 0;
      response.accounts[0].detail!.breakEvenPrices = [];
      return response;
    });
    const user = userEvent.setup();
    render(<RiskPage />);
    await user.click(await screen.findByRole("button", { name: "Open Main risk details" }));
    const dialog = await screen.findByRole("dialog", { name: "Main · Account details" });
    expect(within(dialog).getByText("P&L Breakeven").nextElementSibling?.textContent).toBe("No positions at valuation");
    expect(within(dialog).getByRole("img", { name: "Margin Ratio by BTC Index" }).querySelector(".breakeven-line")).toBeNull();
  });

  it("shows an unavailable breakeven without drawing chart lines", async () => {
    installFetch("ok", (response) => {
      response.accounts[0].detail!.breakEvenPrices = null;
      return response;
    });
    const user = userEvent.setup();
    render(<RiskPage />);
    await user.click(await screen.findByRole("button", { name: "Open Main risk details" }));
    const dialog = await screen.findByRole("dialog", { name: "Main · Account details" });
    expect(within(dialog).getByText("P&L Breakeven").nextElementSibling?.textContent).toBe("—");
    expect(within(dialog).getByRole("img", { name: "Margin Ratio by BTC Index" }).querySelector(".breakeven-line")).toBeNull();
  });

  it("shows each warning label once when multiple positions have the same issue", async () => {
    installFetch("ok", (response) => {
      response.accounts[0].scenarios![0].warnings = [
        { code: "expired_position_settled", context: { symbol: "BTC-260802-60000-P" } },
        { code: "expired_position_settled", context: { symbol: "BTC-260808-65000-C" } },
        { code: "expiry_settlement_approximation", context: { symbol: "BTC-260802-60000-P" } },
        { code: "expiry_settlement_approximation", context: { symbol: "BTC-260808-65000-C" } }
      ];
      return response;
    });
    const user = userEvent.setup();
    render(<RiskPage />);
    await user.click(await screen.findByRole("button", { name: "Open Main risk details" }));
    const dialog = await screen.findByRole("dialog", { name: "Main · Account details" });

    expect(within(dialog).getAllByText("An expired position was removed from margin and settled as cash flow.")).toHaveLength(1);
    expect(within(dialog).getAllByText("Expired positions use the scenario index as an approximate settlement price.")).toHaveLength(1);
  });

  it("changes valuation presets, renders custom local time, and traps focus", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);
    await user.click(await screen.findByRole("button", { name: "Open Main risk details" }));
    const dialog = await screen.findByRole("dialog", { name: "Main · Account details" });
    const beforePlusDay = Date.now() + 86_400_000;
    await user.click(within(dialog).getByRole("button", { name: "+1d" }));
    await waitFor(() => expect(Number(requests.at(-1)?.body?.valuationTimeMs)).toBeGreaterThanOrEqual(beforePlusDay));
    await user.click(within(dialog).getByRole("button", { name: "Custom" }));
    const input = within(dialog).getByLabelText("Custom time") as HTMLInputElement;
    expect(input.value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    await user.clear(input);
    await user.type(input, "2030-01-02T03:04");
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ valuationTimeMs: new Date("2030-01-02T03:04").getTime() }));
    const close = within(dialog).getByRole("button", { name: "Close account details" });
    close.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).not.toBe(document.body);
  });

  it("adds, filters, hides, restores, and persists every visible account with its menu id", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);
    await screen.findByRole("button", { name: "Open Main risk details" });

    expect(screen.getByRole("button", { name: "Hide Main" })).not.toBeNull();
    const accountRow = document.querySelector(".account-filter-row") as HTMLElement;
    expect(accountRow.lastElementChild?.classList.contains("account-add-control")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    const addMenu = screen.getByRole("menu");
    expect(addMenu.closest(".account-add-control")).not.toBeNull();
    expect(addMenu.closest(".account-filter-tabs")).toBeNull();
    expect(within(addMenu).getByText("income")).not.toBeNull();
    await user.click(screen.getByRole("menuitem", { name: /Income/ }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["main", "income"] }));
    expect(await screen.findByRole("button", { name: "Open Income risk details" })).not.toBeNull();
    expect(screen.getAllByRole("button", { name: "Hide Income" })).toHaveLength(1);
    expect(document.querySelector(".multi-account-overview")?.getAttribute("data-layout")).toBe("double");
    expect(window.localStorage.getItem("btc-options-risk-visible-accounts-v1")).toBe('["main","income"]');

    await user.click(screen.getByRole("button", { name: "Hide Main" }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["income"] }));
    expect(window.localStorage.getItem("btc-options-risk-visible-accounts-v1")).toBe('["income"]');

    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    const restoreMenu = screen.getByRole("menu");
    expect(within(restoreMenu).getByText("main")).not.toBeNull();
    await user.click(within(restoreMenu).getByRole("menuitem", { name: /Main/ }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["income", "main"] }));

    await user.click(screen.getByRole("button", { name: "Income" }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["income"] }));
    expect(window.localStorage.getItem("btc-options-risk-active-account-v1")).toBe("income");

    await user.click(screen.getByRole("button", { name: "Hide Income" }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["main"] }));
    expect(window.localStorage.getItem("btc-options-risk-active-account-v1")).toBe("all");
    expect(screen.queryByRole("button", { name: "Open Income risk details" })).toBeNull();
  });

  it("restores stored order, removes unknown ids, keeps known configuration failures, and validates active account", async () => {
    window.localStorage.setItem("btc-options-risk-visible-accounts-v1", JSON.stringify(["ghost", "income", "main", "broken"]));
    window.localStorage.setItem("btc-options-risk-active-account-v1", "ghost");
    const { requests } = installFetch();
    render(<RiskPage />);

    await waitFor(() => expect(requests.find((item) => item.url.endsWith("/evaluate"))?.body).toMatchObject({
      accountIds: ["main", "income", "broken"]
    }));
    expect(window.localStorage.getItem("btc-options-risk-visible-accounts-v1")).toBe('["main","income","broken"]');
    expect(window.localStorage.getItem("btc-options-risk-active-account-v1")).toBe("all");
    expect((screen.getByRole("button", { name: "Add visible account" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps account colors stable and splits Lower/Upper chart lines at null scenarios", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);
    await screen.findByRole("img", { name: "95% Trigger Bounds by IV Shift" });
    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    await user.click(screen.getByRole("menuitem", { name: /Income/ }));
    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    await user.click(screen.getByRole("menuitem", { name: /Hedge/ }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["main", "income", "hedge"] }));
    expect(document.querySelector(".multi-account-overview")?.getAttribute("data-layout")).toBe("multi");
    expect(screen.getByRole("tooltip").textContent).toContain("Reference IV: average of position-weighted account markIV");

    await waitFor(() => expect(document.querySelectorAll('path[data-account="income"][data-direction="upper"]')).toHaveLength(2));
    expect(document.querySelectorAll('path[data-account="main"][data-direction="lower"]')).toHaveLength(1);
    expect(document.querySelectorAll('.overview-boundary-account-label[data-account="main"]')).toHaveLength(2);
    expect(document.querySelectorAll('.overview-boundary-account-label[data-account="income"]')).toHaveLength(2);
    expect(Array.from(document.querySelectorAll('.overview-boundary-account-label[data-account="income"]'), (element) => element.textContent)).toEqual(["Income", "Income"]);
    expect(await screen.findByText("(58.7%)")).not.toBeNull();
    expect(Array.from(
      document.querySelectorAll(".overview-iv-axis-label tspan + tspan"),
      (element) => element.textContent ?? ""
    ).some((label) => label.includes("–"))).toBe(false);
    const mainColor = (document.querySelector('path[data-account="main"]') as SVGPathElement).getAttribute("style");

    await user.click(screen.getByRole("button", { name: "Main" }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({ accountIds: ["main"] }));
    await waitFor(() => expect(document.querySelectorAll('path[data-account="income"]')).toHaveLength(0));
    expect((document.querySelector('path[data-account="main"]') as SVGPathElement).getAttribute("style")).toBe(mainColor);
  });

  it("uses account source times and preserves healthy cards during a partial account failure", async () => {
    const now = Date.now();
    installFetch("ok", (response) => {
      response.accounts.forEach((account, index) => {
        if (account.current) account.current.sourceTime = now - (index ? 12_000 : 1_000);
      });
      const failed = response.accounts.find((account) => account.id === "hedge");
      if (failed) {
        failed.status = "unavailable";
        failed.error = { code: "upstream_unavailable" };
        failed.current = null;
        failed.reconciliation = null;
        failed.scenarios = null;
        failed.detail = null;
      }
      return response;
    });
    const user = userEvent.setup();
    render(<RiskPage />);
    const main = await screen.findByRole("button", { name: "Open Main risk details" });
    expect(main.textContent).toContain("Updated just now");
    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    await user.click(screen.getByRole("menuitem", { name: /Income/ }));
    const income = await screen.findByRole("button", { name: "Open Income risk details" });
    expect(income.textContent).toContain("Updated 12s ago");
    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    await user.click(screen.getByRole("menuitem", { name: /Hedge/ }));
    const hedge = await screen.findByRole("button", { name: "Open Hedge risk details" });
    expect(main.querySelector(".live-indicator.live")?.getAttribute("aria-label")).toBe("API status: LIVE");
    expect(income.querySelector(".live-indicator.live")?.getAttribute("aria-label")).toBe("API status: LIVE");
    expect(hedge.querySelector(".live-indicator.unavailable")?.getAttribute("aria-label")).toBe("API status: UNAVAILABLE");
    expect(hedge.textContent).toContain("Binance account data is temporarily unavailable.");
    expect(hedge.querySelector(".risk-reservoir")).toBeNull();
    expect(screen.getByText("API · PARTIAL")).not.toBeNull();
  });

  it("inherits the exact overview valuation time when opening Focus", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);
    await screen.findByRole("button", { name: "Open Main risk details" });
    const overviewToolbar = screen.getByText("Valuation time").closest(".overview-valuation-control") as HTMLElement;
    await user.click(within(overviewToolbar).getByRole("button", { name: "+7d" }));
    await waitFor(() => expect(requests.filter((item) => item.url.endsWith("/evaluate"))).toHaveLength(2));
    const overviewTime = requests.at(-1)?.body?.valuationTimeMs;
    await user.click(await screen.findByRole("button", { name: "Open Main risk details" }));
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({
      accountIds: ["main"],
      detailAccountId: "main",
      detailIvShiftVolPoints: 0,
      valuationTimeMs: overviewTime
    }));
  });

  it("shows actual scan bounds for missing triggers and clamps only the reservoir fill", async () => {
    installFetch("ok", (response) => {
      response.accounts[0].current!.marginRatio = 1.28;
      response.accounts[0].scenarios!.forEach((scenario) => {
        scenario.lowerTrigger = null;
        scenario.upperTrigger = null;
        scenario.triggers = [];
      });
      return response;
    });
    render(<RiskPage />);
    const card = await screen.findByRole("button", { name: "Open Main risk details" });
    expect(card.textContent).toContain("128.0%");
    expect(card.textContent).toContain("Safe");
    expect(card.textContent).not.toContain("No boundary");
    expect(card.textContent).not.toContain("None");
    expect(card.querySelectorAll(".risk-boundary-grid .empty")).toHaveLength(2);
    expect(card.textContent).toContain("12,400–124,000 scan");
    expect((card as HTMLElement).style.getPropertyValue("--risk-level")).toBe("100%");
    expect((card as HTMLElement).style.getPropertyValue("--risk-visual-level")).toBe("");
    expect(card.querySelector(".risk-reservoir")?.closest(".risk-water-zone")).not.toBeNull();
    expect(card.querySelector(".risk-card-heading")?.closest(".risk-water-zone")).toBeNull();
    expect(card.querySelectorAll(".risk-water-zone .risk-threshold")).toHaveLength(2);
    expect(await screen.findByText("No trigger bounds in scan")).not.toBeNull();
  });

  it("uses a danger accent only above the 80% margin-ratio threshold", async () => {
    window.localStorage.setItem("btc-options-risk-visible-accounts-v1", JSON.stringify(["main", "income"]));
    installFetch("ok", (response) => {
      response.accounts.forEach((account) => {
        if (!account.current) return;
        account.current.marginRatio = account.id === "main" ? 0.8 : 0.801;
      });
      return response;
    });
    render(<RiskPage />);

    const main = await screen.findByRole("button", { name: "Open Main risk details" });
    const income = await screen.findByRole("button", { name: "Open Income risk details" });
    expect(main.classList.contains("elevated-risk")).toBe(false);
    expect(income.classList.contains("elevated-risk")).toBe(true);
  });

  it("keeps every computable point but labels only meaningful changes", async () => {
    installFetch("ok", (response) => {
      response.accounts[0].scenarios!.forEach((scenario) => {
        if (scenario.ivShiftVolPoints < -30) {
          scenario.status = "unavailable";
          return;
        }
        scenario.lowerTrigger = 51_240;
        scenario.upperTrigger = null;
        scenario.triggers = [{ price: 51_240, direction: "lower", reason: "margin_ratio_threshold" }];
      });
      return response;
    });
    render(<RiskPage />);
    await screen.findByRole("img", { name: "95% Trigger Bounds by IV Shift" });
    await waitFor(() => expect(document.querySelectorAll(".overview-boundary-point")).toHaveLength(19));
    expect(document.querySelectorAll(".overview-boundary-label")).toHaveLength(2);
    expect(document.querySelectorAll(".overview-boundary-point + title")).toHaveLength(19);
  });

  it("fits the trigger chart to wide containers and keeps a readable mobile minimum", async () => {
    const callbacks: ResizeObserverCallback[] = [];
    const observedElements: Element[] = [];
    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        callbacks.push(callback);
      }
      observe(element: Element) {
        observedElements.push(element);
      }
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
    installFetch();
    render(<RiskPage />);

    const chart = await screen.findByRole("img", { name: "95% Trigger Bounds by IV Shift" });
    const container = observedElements[0] as HTMLDivElement;
    expect(chart.style.width).toBe("1100px");

    Object.defineProperties(container, {
      clientWidth: { configurable: true, value: 1_220 },
      clientHeight: { configurable: true, value: 250 }
    });
    act(() => callbacks[0]([], {} as ResizeObserver));
    await waitFor(() => expect(chart.style.width).toBe("1200px"));

    Object.defineProperty(container, "clientWidth", { configurable: true, value: 600 });
    act(() => callbacks[0]([], {} as ResizeObserver));
    await waitFor(() => expect(chart.style.width).toBe("1100px"));
  });

  it("reattaches chart sizing after an empty valuation and restores the filled chart", async () => {
    const observedElements: Element[] = [];
    let disconnectCount = 0;
    class MockResizeObserver {
      observe(element: Element) {
        observedElements.push(element);
      }
      disconnect() {
        disconnectCount += 1;
      }
    }
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
    let evaluateCount = 0;
    installFetch("ok", (response) => {
      evaluateCount += 1;
      if (evaluateCount === 2) {
        response.accounts.forEach((account) => account.scenarios?.forEach((scenario) => {
          scenario.lowerTrigger = null;
          scenario.upperTrigger = null;
          scenario.triggers = [];
        }));
      }
      return response;
    });
    const user = userEvent.setup();
    render(<RiskPage />);

    await screen.findByRole("img", { name: "95% Trigger Bounds by IV Shift" });
    expect(observedElements).toHaveLength(1);
    const toolbar = screen.getByText("Valuation time").closest(".overview-valuation-control") as HTMLElement;
    await user.click(within(toolbar).getByRole("button", { name: "+7d" }));
    expect(await screen.findByText("No trigger bounds in scan")).not.toBeNull();
    expect(disconnectCount).toBeGreaterThanOrEqual(1);

    await user.click(within(toolbar).getByRole("button", { name: "Now" }));
    await screen.findByRole("img", { name: "95% Trigger Bounds by IV Shift" });
    expect(observedElements).toHaveLength(2);
    expect(observedElements[1]).not.toBe(observedElements[0]);
  });

  it("opens Focus for the clicked non-default account and force refreshes all visible accounts from the page header", async () => {
    const { requests } = installFetch();
    const user = userEvent.setup();
    render(<RiskPage />);
    await screen.findByRole("button", { name: "Open Main risk details" });
    await user.click(screen.getByRole("button", { name: "Add visible account" }));
    await user.click(screen.getByRole("menuitem", { name: /Income/ }));
    const income = await screen.findByRole("button", { name: "Open Income risk details" });
    await user.click(income);
    await waitFor(() => expect(requests.at(-1)?.body).toMatchObject({
      accountIds: ["income"],
      detailAccountId: "income",
      detailIvShiftVolPoints: 0
    }));
    await user.keyboard("{Escape}");

    const accountRequestCount = requests.filter((request) => request.url.endsWith("/accounts")).length;
    await user.click(screen.getByTitle("Refresh now"));
    await waitFor(() => expect(requests.filter((request) => request.url.endsWith("/accounts"))).toHaveLength(accountRequestCount + 1));
    await waitFor(() => expect(requests.filter((request) => request.url.endsWith("/evaluate")).at(-1)?.body).toMatchObject({
      accountIds: ["main", "income"],
      forceRefresh: true
    }));
  });

  it("does not poll a non-Now overview", async () => {
    vi.useFakeTimers();
    const { requests } = installFetch();
    render(<RiskPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const overviewToolbar = screen.getByText("Valuation time").closest(".overview-valuation-control") as HTMLElement;
    fireEvent.click(within(overviewToolbar).getByRole("button", { name: "+1d" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const count = requests.filter((request) => request.url.endsWith("/evaluate")).length;
    await act(async () => vi.advanceTimersByTimeAsync(REFRESH_INTERVAL * 2));
    expect(requests.filter((request) => request.url.endsWith("/evaluate"))).toHaveLength(count);
  });

  it("pauses overview polling while Focus is open and does not poll a non-Now Focus", async () => {
    vi.useFakeTimers();
    const { requests } = installFetch();
    render(<RiskPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const card = screen.getByRole("button", { name: "Open Main risk details" });
    fireEvent.click(card);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const dialog = screen.getByRole("dialog", { name: "Main · Account details" });
    const beforeShortTick = requests.filter((request) => request.url.endsWith("/evaluate")).length;
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.getByRole("dialog", { name: "Main · Account details" })).toBe(dialog);
    expect(screen.getByRole("button", { name: "Open Main risk details" })).toBe(card);
    expect(requests.filter((request) => request.url.endsWith("/evaluate"))).toHaveLength(beforeShortTick);

    const beforeNowPoll = requests.filter((request) => request.url.endsWith("/evaluate")).length;
    await act(async () => vi.advanceTimersByTimeAsync(REFRESH_INTERVAL));
    expect(requests.filter((request) => request.url.endsWith("/evaluate"))).toHaveLength(beforeNowPoll + 1);
    expect(requests.filter((request) => request.url.endsWith("/evaluate")).at(-1)?.body).toMatchObject({
      accountIds: ["main"],
      detailAccountId: "main"
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "+1d" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const beforeNonNow = requests.filter((request) => request.url.endsWith("/evaluate")).length;
    await act(async () => vi.advanceTimersByTimeAsync(REFRESH_INTERVAL * 2));
    expect(requests.filter((request) => request.url.endsWith("/evaluate"))).toHaveLength(beforeNonNow);
  });

  it("aborts an obsolete overview request and ignores its late response", async () => {
    let firstSignal: AbortSignal | undefined;
    let resolveFirst!: (response: Response) => void;
    let evaluateCount = 0;
    const firstResponse = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/session")) return jsonResponse({ authenticated: true });
      if (url.endsWith("/accounts")) return jsonResponse(ACCOUNTS);
      evaluateCount += 1;
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      if (evaluateCount === 1) {
        firstSignal = init?.signal ?? undefined;
        return firstResponse;
      }
      const latest = evaluation(0, "ok", ["main"], [0, 10, 20, 30, 40, 50], null);
      latest.valuationTimeMs = Number(body.valuationTimeMs);
      latest.accounts[0].scenarios![0].lowerTrigger = 55_555;
      return jsonResponse(latest);
    }));
    render(<RiskPage />);
    await waitFor(() => expect(evaluateCount).toBe(1));
    const overviewToolbar = screen.getByText("Valuation time").closest(".overview-valuation-control") as HTMLElement;
    fireEvent.click(within(overviewToolbar).getByRole("button", { name: "+1d" }));
    const card = await screen.findByRole("button", { name: "Open Main risk details" });
    expect(card.textContent).toContain("55,555");
    expect(firstSignal?.aborted).toBe(true);

    const old = evaluation(0, "ok", ["main"], [0, 10, 20, 30, 40, 50], null);
    old.accounts[0].scenarios![0].lowerTrigger = 11_111;
    resolveFirst(jsonResponse(old));
    await act(async () => { await Promise.resolve(); });
    expect(card.textContent).toContain("55,555");
    expect(card.textContent).not.toContain("11,111");
  });

  it("keeps unreconciled fields explicit and hides scenario detail", async () => {
    installFetch("unreconciled");
    const user = userEvent.setup();
    render(<RiskPage />);
    const card = await screen.findByRole("button", { name: "Open Main risk details" });
    expect(card.textContent).toContain("Scenario unavailable");
    await user.click(card);
    expect(await screen.findByText("Maintenance margin is unreconciled; scenario outputs are unavailable.")).not.toBeNull();
    expect(screen.getAllByText("Scenario unavailable").length).toBeGreaterThan(0);
    expect(screen.queryByText("Maintenance margin contribution")).toBeNull();
  });

  it("labels stale triggered and unsupported states without placeholder scenario values", async () => {
    const stale = installFetch("ok", (response) => {
      response.market.stale = true;
      response.accounts[0].current!.stale = true;
      response.accounts[0].current!.triggeredNow = true;
      response.accounts[0].current!.triggerReason = "margin_ratio_threshold";
      return response;
    });
    const { unmount } = render(<RiskPage />);
    const card = await screen.findByRole("button", { name: "Open Main risk details" });
    expect(card.querySelector(".live-indicator.stale")?.getAttribute("aria-label")).toBe("API status: STALE");
    expect(card.textContent).toContain("Triggered now");
    unmount();
    vi.unstubAllGlobals();

    installFetch("ok", (response) => {
      response.accounts[0].status = "unsupported";
      response.accounts[0].error = { code: "unsupported_margin_mode" };
      response.accounts[0].scenarios = null;
      response.accounts[0].detail = null;
      return response;
    });
    render(<RiskPage />);
    const unsupported = await screen.findByRole("button", { name: "Open Main risk details" });
    expect(unsupported.querySelector(".live-indicator.unsupported")?.getAttribute("aria-label")).toBe("API status: UNSUPPORTED");
    expect(unsupported.textContent).toContain("Scenario unavailable");
    expect(stale.requests.length).toBeGreaterThan(0);
  });

  it("polls the visible Now overview every 30 seconds", async () => {
    vi.useFakeTimers();
    const { requests } = installFetch();
    render(<RiskPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const initialEvaluateCount = requests.filter((item) => item.url.endsWith("/evaluate")).length;
    await act(async () => vi.advanceTimersByTimeAsync(REFRESH_INTERVAL));
    expect(requests.filter((item) => item.url.endsWith("/evaluate")).length).toBeGreaterThan(initialEvaluateCount);
  });
});

const REFRESH_INTERVAL = 30_000;
