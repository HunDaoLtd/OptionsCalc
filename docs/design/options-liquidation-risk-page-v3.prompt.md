# Liquidation Risk overview v3 prompt

Use case: ui-mockup

Input images: Image 1 is the approved Liquidation Risk page v2 edit target.

Primary request: Refine only the reservoir progress visualization inside the three account summary cards. Preserve the entire page, chart, metrics, controls, typography, and layout unchanged.

## Required reservoir style

- Treat each entire card height as a 0–100% vertical margin-ratio reservoir.
- Use a flat, translucent pale aqua fill rising from the card bottom.
- The top boundary of the fill is a restrained, low-amplitude repeating water ripple: a clean smooth sine-like wave with only 2–3px vertical amplitude, not a straight line and not realistic water.
- Center the ripple around the exact current ratio: Main 72.8%, Income 54.2%, Hedge 18.6%.
- Place the teal current-value tag “72.8%”, “54.2%”, or “18.6%” at the right edge aligned with the average height of its ripple.
- Draw the 80% threshold as a thin yellow dashed horizontal line across the full card, with yellow dot and label “80% Alert” at the right.
- Draw the 95% threshold as a thin coral-red dashed horizontal line across the full card, with red dot and label “95% Liquidation” at the right.
- The Main ripple must sit visibly below the 80% dashed line; do not merge the waterline and warning threshold.
- Remove all numeric scale labels on the left such as 0%, 50%, 80%, 90%, 95%, 00%, and 100%.
- Keep the small generic label “Margin Ratio” below each account title.
- Keep card content above the translucent fill and fully readable: trigger price, distance, Adjusted Equity, and Maint. Margin.
- Preserve the Main card coral top accent.

## Visual constraints

- The water effect is subtle flat UI decoration only: no bubbles, splashes, reflections, photorealism, strong gradients, or animation.
- Dashed threshold lines must be visibly distinct from the teal ripple.
- Change nothing outside the card progress graphics.
- Do not alter any text values or chart data.
- No conventional horizontal progress bars, no gauges, no watermark.
