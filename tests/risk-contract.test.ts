import { describe, expect, it } from "vitest";
import {
  RISK_ACCOUNT_CONFIG_STATUSES,
  RISK_ACCOUNT_EVALUATION_STATUSES,
  RISK_API_ERROR_CODES,
  RISK_ISSUE_CODES,
  RISK_RECONCILIATION_STATUSES,
  RISK_SCENARIO_STATUSES,
  RISK_TRIGGER_DIRECTIONS,
  RISK_TRIGGER_REASONS
} from "../src/types";
import type {
  LiquidationRiskAccountsResponse,
  LiquidationRiskEvaluateRequest,
  LiquidationRiskEvaluateResponse,
  RiskApiErrorResponse
} from "../src/types";

describe("liquidation risk wire contract", () => {
  it("freezes status and trigger enums", () => {
    expect(RISK_ACCOUNT_CONFIG_STATUSES).toEqual([
      "configured",
      "missing_credentials",
      "configuration_error"
    ]);
    expect(RISK_ACCOUNT_EVALUATION_STATUSES).toEqual([
      "ok",
      "unreconciled",
      "unsupported",
      "unavailable"
    ]);
    expect(RISK_RECONCILIATION_STATUSES).toEqual([
      "matched",
      "warning",
      "unreconciled",
      "unsupported"
    ]);
    expect(RISK_SCENARIO_STATUSES).toEqual(["ok", "unavailable"]);
    expect(RISK_TRIGGER_REASONS).toEqual([
      "margin_ratio_threshold",
      "non_positive_adjusted_equity",
      "negative_wallet_long_value_ratio"
    ]);
    expect(RISK_TRIGGER_DIRECTIONS).toEqual(["lower", "upper"]);
    expect(RISK_API_ERROR_CODES).toEqual(["invalid_request", "service_unavailable"]);
    expect(RISK_ISSUE_CODES).toEqual([
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
    ]);
  });

  it("accepts the frozen accounts and evaluate request shapes", () => {
    const accounts = {
      defaultAccountId: "main",
      accounts: [{ id: "main", label: "Main", status: "configured" }]
    } satisfies LiquidationRiskAccountsResponse;
    const request = {
      accountIds: ["main"],
      valuationTimeMs: 1785542400000,
      ivShiftsVolPoints: [0, 10, 20],
      detailAccountId: "main",
      detailIvShiftVolPoints: 10,
      forceRefresh: false
    } satisfies LiquidationRiskEvaluateRequest;

    expect(accounts.defaultAccountId).toBe("main");
    expect(request.detailIvShiftVolPoints).toBe(10);
  });

  it("accepts a successful evaluation with explicit detail IV", () => {
    const response = {
      updatedAt: 1785542401000,
      market: {
        indexPrice: 62885.16,
        updatedAt: 1785542400000,
        stale: false
      },
      valuationTimeMs: 1785542400000,
      scan: {
        minimum: 12577.032,
        maximum: 125770.32,
        sampleCount: 2001,
        boundaryTolerance: 1
      },
      accounts: [
        {
          id: "main",
          label: "Main",
          status: "ok",
          referenceIv: 0.586153846153846,
          error: null,
          current: {
            adjustedEquity: 18263.454137,
            maintenanceMargin: 13295.794612,
            marginRatio: 0.728,
            derivedWalletBalance: 17253.454137,
            longOptionValue: 1010,
            unrealizedPnl: -674,
            sourceTime: 1785542400200,
            stale: false,
            triggeredNow: false,
            triggerReason: null
          },
          reconciliation: {
            status: "matched",
            mmDriftPercent: 0,
            coveredPositions: 6,
            totalPositions: 6,
            sourceSkewMs: 700
          },
          scenarios: [
            {
              ivShiftVolPoints: 10,
              status: "ok",
              lowerTrigger: 51240,
              upperTrigger: null,
              triggers: [
                {
                  price: 51240,
                  direction: "lower",
                  reason: "margin_ratio_threshold"
                }
              ],
              warnings: []
            }
          ],
          detail: {
            ivShiftVolPoints: 10,
            breakEvenPrices: [51240, 82500],
            remainingPositionCount: 6,
            curve: [
              {
                indexPrice: 62885.16,
                adjustedEquity: 18263.454137,
                maintenanceMargin: 13295.794612,
                marginRatio: 0.728,
                triggered: false,
                triggerReason: null
              }
            ],
            maintenanceMarginContributions: [
              {
                symbol: "BTC-260802-60000-P",
                optionSide: "PUT",
                positionSide: "SHORT",
                quantity: -1.2,
                entryPrice: 750,
                scenarioOptionPrice: 650,
                maintenanceMargin: 4696.4877648,
                maintenanceMarginShare: 0.3532,
                expiryMs: 1785657600000
              }
            ],
            triggers: [
              {
                price: 51240,
                direction: "lower",
                reason: "margin_ratio_threshold"
              }
            ],
            warnings: []
          },
          warnings: []
        }
      ],
      warnings: [{ code: "open_orders_not_included" }]
    } satisfies LiquidationRiskEvaluateResponse;

    expect(response.accounts[0].detail?.ivShiftVolPoints).toBe(10);
    expect(response.accounts[0].detail?.breakEvenPrices).toEqual([51240, 82500]);
    expect(response.accounts[0].detail?.remainingPositionCount).toBe(6);
    expect(response.accounts[0].scenarios?.[0].triggers[0].direction).toBe("lower");
  });

  it("keeps unavailable accounts and typed API errors addressable", () => {
    const partialResponse = {
      updatedAt: 1785542401000,
      market: {
        indexPrice: 62885.16,
        updatedAt: 1785542400000,
        stale: false
      },
      valuationTimeMs: 1785542400000,
      scan: {
        minimum: 12577.032,
        maximum: 125770.32,
        sampleCount: 2001,
        boundaryTolerance: 1
      },
      accounts: [
        {
          id: "income",
          label: "Income",
          status: "unavailable",
          referenceIv: null,
          error: { code: "missing_credentials" },
          current: null,
          reconciliation: null,
          scenarios: null,
          detail: null,
          warnings: []
        }
      ],
      warnings: []
    } satisfies LiquidationRiskEvaluateResponse;
    const errorResponse = {
      error: {
        code: "invalid_request",
        issues: [
          {
            code: "invalid_detail_iv_shift",
            context: { field: "detailIvShiftVolPoints", value: 15 }
          }
        ]
      }
    } satisfies RiskApiErrorResponse;

    expect(partialResponse.accounts[0].current).toBeNull();
    expect(errorResponse.error.issues[0].code).toBe("invalid_detail_iv_shift");
  });
});
