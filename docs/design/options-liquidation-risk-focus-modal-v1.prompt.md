<!-- @format -->

# Liquidation Risk Focus modal v1 prompt

Use case: ui-mockup

Asset type: high-fidelity Focus account-detail modal state

Input images: Image 1 is the approved Liquidation Risk overview page v3 and must remain visible as the dimmed background.

Primary request: Show the result of clicking the Main account card: open one large centered modal for detailed risk analysis. The modal should feel like part of the same compact internal data tool, not a separate page.

## Backdrop

- Preserve Image 1 exactly behind the modal.
- Apply a subtle translucent charcoal overlay and very slight blur so the page structure remains recognizable but clearly inactive.
- Keep the browser viewport and 16:10 desktop composition.

## Modal

- Center a large white modal approximately 1220px wide and 760px tall, with 10px radius, warm beige 1px border, restrained shadow, and generous but compact 22px padding.
- Modal header: title “Main · Account details”, green dot and “API · LIVE”, small text “Updated 8s ago”, and a clear close X button at top-right.
- Beneath the header, a compact scenario toolbar: “Valuation time” with “Now” active, “+1d”, “+7d”, “Custom”; “IV shift” with value “+0 vol”; outlined button “Refresh account”.
- Do not include account switching inside the modal.

## Top summary strip

- Metrics: “BTC Index” “62,885.16”; “Current Margin Ratio” “72.8%”; “95% Trigger Price” “51,240”; “Distance from index” “-18.5%”.
- Make 72.8% teal and the trigger price/distance coral.
- Add a thin miniature reservoir indicator next to Current Margin Ratio: translucent aqua fill at 72.8%, yellow dashed marker “80%”, coral dashed marker “95%”.

## Main modal body

- Left chart title “Margin Ratio by BTC Index”; subtitle “Current markIV · Valuation time: Now”.
- Horizontal axis “BTC Index (USDT)” with ticks 45,000 through 70,000; vertical axis “Margin Ratio” with 40%, 60%, 80%, 95%, 110%.
- Draw one smooth risk curve falling as BTC Index rises.
- Yellow dashed horizontal line at 80% labeled “Margin call”.
- Coral-red dashed horizontal line at 95% labeled “Liquidation threshold”.
- Mark x=51,240, y=95% as “Trigger 51,240”; mark x=62,885, y=72.8% as “Current 72.8%”.
- Add a faint vertical dashed line at the current BTC Index; no filled chart area.
- Right panel “Margin & equity”: Adjusted Equity 184,320; Maintenance Margin 134,210; Wallet Balance 181,060; Long Option Value 3,260; Unrealized PNL -6,740.
- Right panel “Reconciliation”: “Matched with Binance”; AE drift 0.00%; MM drift 0.03%; Positions 6 / 6; “Classic Options Margin”.

## Bottom table

- Title “Maintenance margin contribution”.
- Headers: Symbol, Side, Qty, Mark Price, Maint. Margin, MM Share.
- Rows:
  - BTC-260925-60000-P | Short | -1.2 | 2,418.6 | 61,840 | 46.1%
  - BTC-260828-62000-P | Short | -0.8 | 1,982.2 | 43,760 | 32.6%
  - BTC-260821-61000-P | Short | -0.5 | 1,204.5 | 28,610 | 21.3%
- Footer: “Showing top 3 of 6 positions” and “Calculated value · not an execution price”.

## Constraints

- Match the overview typography, colors, radii, spacing, and density.
- The modal must clearly read as an overlay, not a new route.
- No additional charts, account-management controls, trading buttons, order entry, decoration, dark theme, or watermark.
- Do not alter the underlying overview page except dimming it.
