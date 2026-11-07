/** @format */

export const en = {
  common: {
    "app.title": "BTC Options Toolkit",
    "brand.eyebrow": "Binance BTC European Options",
    "nav.label": "Toolkit",
    "nav.yield": "Put Yield Matrix",
    "nav.risk": "Liquidation Risk",
    "theme.label": "Color theme",
    "theme.option": "{{theme}} theme",
    "language.label": "Language",
    "language.zh": "Chinese",
    "language.en": "English",
    "refresh.title": "Refresh now",
    "refresh.label": "Refresh",
    "updated.waiting": "Waiting for data",
    "updated.justNow": "Updated just now",
    "updated.seconds": "Updated {{count}}s ago",
    "updated.minutes": "Updated {{count}}m ago",
    "updated.hours": "Updated {{count}}h ago",
  },
  yield: {
    Reference: "Reference",
    "Margin / Contract": "Margin / Contract",
    "Nearby Strikes": "Nearby Strikes",
    "Strike Interval": "Strike Interval",
    "BTC Index (USDT)": "BTC Index (USDT)",
    "Yield Mode": "Yield Mode",
    "Yield mode": "Yield mode",
    Actual: "Actual",
    Secured: "Secured",
    "Showing last successful snapshot": "Showing last successful snapshot",
    Expiry: "Expiry",
    Days: "Days",
    "Lock or unlock this strike": "Lock or unlock this strike",
    "expiry strike label": "{{expiry}} strike {{strike}}",
    "No BTC Put contracts to display": "No BTC Put contracts to display",
    "Empty API response": "Empty API response",
    "Load failed": "Load failed",
    "Load timed out": "Load timed out",
    "Market data error": "Market data error: {{detail}}",
  },
  risk: {
    "Private account data": "Private account data",
    "Unlock Liquidation Risk": "Unlock Liquidation Risk",
    "Sign in once on this device. Access remains available until you sign out, revoke the session, or the browser removes its cookie.":
      "Sign in once on this device. Access remains available until you sign out, revoke the session, or the browser removes its cookie.",
    "Checking Risk access…": "Checking Risk access…",
    "Access password": "Access password",
    "Unlocking…": "Unlocking…",
    Unlock: "Unlock",
    "Try again": "Try again",
    "Sign out": "Sign out",
    "Risk authentication is temporarily unavailable.":
      "Risk authentication is temporarily unavailable.",
    "Unable to reach Risk authentication.":
      "Unable to reach Risk authentication.",
    "Risk authentication returned an unreadable response.":
      "Risk authentication returned an unreadable response.",
    "The Risk authentication response is invalid.":
      "The Risk authentication response is invalid.",
    "Risk authentication did not complete.":
      "Risk authentication did not complete.",
    "Risk login failed.": "Risk login failed.",
    "Risk logout failed.": "Risk logout failed.",
    "Your Risk session ended. Enter the access password again.":
      "Your Risk session ended. Enter the access password again.",
    "The risk service is unavailable.": "The risk service is unavailable.",
    Safe: "Safe",
    "N/A": "N/A",
    UNAVAILABLE: "UNAVAILABLE",
    STALE: "STALE",
    LIVE: "LIVE",
    PARTIAL: "PARTIAL",
    "Scan range unavailable": "Scan range unavailable",
    "scan range": "{{minimum}}–{{maximum}} scan",
    "95% Liquidation": "95% Liquidation",
    "80% Alert": "80% Alert",
    "Margin Ratio": "Margin Ratio",
    "Open account risk details": "Open {{account}} risk details",
    "distance from index": "{{distance}} from index",
    "API status": "API status: {{status}}",
    "Refreshing…": "Refreshing…",
    "Triggered now": "Triggered now",
    "Scenario unavailable": "Scenario unavailable",
    "Lower Trigger": "Lower Trigger",
    "Upper Trigger": "Upper Trigger",
    "Adjusted Equity": "Adjusted Equity",
    "Maint. Margin": "Maint. Margin",
    "Scenario results are blocked until MM reconciles.":
      "Scenario results are blocked until MM reconciles.",
    "Reference IV average":
      "Reference IV: average of position-weighted account markIV",
    "Reference IV single": "Reference IV: position-weighted markIV",
    "95% Trigger Bounds": "95% Trigger Bounds",
    "Reference IV details": "Reference IV details",
    "Calculated scenarios":
      "Calculated from {{count}} account × {{scenarios}} IV scenarios",
    "Calculated scenarios_other":
      "Calculated from {{count}} accounts × {{scenarios}} IV scenarios",
    "Calculated value · not an execution price":
      "Calculated value · not an execution price",
    "Chart legend": "Chart legend",
    "Lower trigger, solid line": "Lower trigger, solid line",
    "Lower trigger": "Lower trigger",
    "Upper trigger, dashed line": "Upper trigger, dashed line",
    "Upper trigger": "Upper trigger",
    "No trigger bounds in scan": "No trigger bounds in scan",
    "95% Trigger Bounds by IV Shift": "95% Trigger Bounds by IV Shift",
    "Trigger chart description":
      "Lower trigger boundaries use solid lines and upper trigger boundaries use dashed lines. The absolute IV axis uses the average reference markIV of the displayed accounts. Missing values are not connected.",
    "Current BTC Index": "Current BTC Index",
    vol: "vol",
    "IV Shift (vol pts)": "IV Shift (vol pts)",
    "BTC Trigger Price (USDT)": "BTC Trigger Price (USDT)",
    "Margin Ratio by BTC Index": "Margin Ratio by BTC Index",
    Lower: "Lower",
    Upper: "Upper",
    "Curve at current index": "Curve at current index",
    "BTC Index (USDT)": "BTC Index (USDT)",
    "95% liquidation threshold": "95% liquidation threshold",
    "80% alert": "80% alert",
    "Account details": "Account details",
    "Close account details": "Close account details",
    "Scenario controls": "Scenario controls",
    "Valuation time": "Valuation time",
    Now: "Now",
    Custom: "Custom",
    "Custom time": "Custom time",
    "IV shift": "IV shift",
    "Refresh account": "Refresh account",
    "Maintenance margin is unreconciled; scenario outputs are unavailable.":
      "Maintenance margin is unreconciled; scenario outputs are unavailable.",
    "This account configuration is unsupported.":
      "This account configuration is unsupported.",
    "BTC Index": "BTC Index",
    "Current Margin Ratio": "Current Margin Ratio",
    "95% Trigger": "95% Trigger",
    "Distance from index": "Distance from index",
    Current: "Current",
    "Current PNL": "Current PNL",
    "Loading scenario detail…": "Loading scenario detail…",
    "The current account result is retained while the selected scenario is calculated.":
      "The current account result is retained while the selected scenario is calculated.",
    "The selected scenario could not be calculated reliably.":
      "The selected scenario could not be calculated reliably.",
    "Current markIV · Valuation time":
      "Current markIV · Valuation time: {{time}}",
    "Margin & equity": "Margin & equity",
    "Derived Wallet": "Derived Wallet",
    "Long Option Value": "Long Option Value",
    "P&L Breakeven": "P&L Breakeven",
    "No breakeven in scan range": "None in scan range",
    "No positions at valuation": "No positions at valuation",
    Reconciliation: "Reconciliation",
    "Matched with Binance": "● Matched with Binance",
    Unavailable: "Unavailable",
    "MM drift": "MM drift",
    Positions: "Positions",
    "Source skew": "Source skew",
    "Margin mode": "Margin mode",
    Unsupported: "Unsupported",
    "Classic Options Margin": "Classic Options Margin",
    "Scenario warnings": "Scenario warnings",
    "Maintenance margin contribution": "Maintenance margin contribution",
    Symbol: "Symbol",
    Side: "Side",
    Qty: "Qty",
    "Entry Price": "Entry Price",
    "Calculated Value": "Calculated Value",
    "MM Share": "MM Share",
    "Showing positions": "Showing {{count}} positions",
    "No contribution detail": "No contribution detail",
    "The risk account request timed out.":
      "The risk account request timed out.",
    "The accounts response is not valid.":
      "The accounts response is not valid.",
    "The risk response is not valid.": "The risk response is not valid.",
    "Unable to reach the risk service.": "Unable to reach the risk service.",
    "The risk service returned an unreadable response.":
      "The risk service returned an unreadable response.",
    "The risk request was rejected.": "The risk request was rejected.",
    "Choose a valid custom valuation time.":
      "Choose a valid custom valuation time.",
    "The risk evaluation timed out.": "The risk evaluation timed out.",
    "The scenario request timed out.": "The scenario request timed out.",
    Accounts: "Accounts",
    "All accounts": "All accounts",
    "Hide account": "Hide {{account}}",
    "Add visible account": "Add visible account",
    "Add account": "Add account",
    "Overview valuation time presets": "Overview valuation time presets",
    "Overview custom valuation time": "Overview custom valuation time",
    "Last successful risk result retained.":
      "Last successful risk result retained.",
    "Visible account risk overview": "Visible account risk overview",
    "Loading account configuration…": "Loading account configuration…",
    "Calculating account scenarios…":
      "Calculating {{count}} account scenarios…",
    "issue.open_orders_not_included":
      "Open orders are not included in this estimate.",
    "issue.market_data_stale": "Market data is stale.",
    "issue.account_data_stale":
      "The last successful account snapshot is being used.",
    "issue.reconciliation_drift_warning":
      "Maintenance margin differs from Binance beyond the matched tolerance.",
    "issue.expiry_settlement_approximation":
      "Expired positions use the scenario index as an approximate settlement price.",
    "issue.near_expiry_trigger_disabled":
      "Trigger estimation is disabled in the final 30 minutes before expiry.",
    "issue.expired_position_settled":
      "An expired position was removed from margin and settled as cash flow.",
    "issue.missing_credentials":
      "Read-only credentials are missing for this account.",
    "issue.configuration_error": "The risk account configuration is invalid.",
    "issue.upstream_unavailable":
      "Binance account data is temporarily unavailable.",
    "issue.stale_data_expired":
      "The last successful account snapshot is too old to use.",
    "issue.unsupported_margin_mode":
      "This account is not using Classic Options Margin.",
    "issue.unsupported_quote_asset":
      "Only USDT-settled option accounts are supported.",
    "issue.non_btc_open_position":
      "A non-BTC open position prevents this risk estimate.",
    "issue.unreconciled_maintenance_margin":
      "Maintenance margin could not be reconciled with Binance.",
    "issue.missing_contract":
      "An open position is missing its contract specification.",
    "issue.missing_mark":
      "An open position is missing current option market data.",
    "issue.invalid_numeric_value": "Binance returned an invalid numeric value.",
    "issue.non_positive_scenario_volatility":
      "The selected IV shift produces a non-positive volatility.",
    "issue.no_eligible_long_option_value":
      "The scenario has no eligible long option value for this trigger check.",
    "issue.invalid_json": "The request body was not valid JSON.",
    "issue.invalid_account_ids": "The selected account list is invalid.",
    "issue.unknown_account_id": "The selected account is not configured.",
    "issue.invalid_valuation_time": "The selected valuation time is invalid.",
    "issue.invalid_iv_shifts": "The selected IV shift is invalid.",
    "issue.invalid_detail_account_id":
      "The requested detail account is invalid.",
    "issue.invalid_detail_iv_shift":
      "The requested detail IV shift is invalid.",
    "issue.invalid_force_refresh": "The refresh option is invalid.",
  },
} as const;
