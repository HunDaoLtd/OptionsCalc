import type { MatrixRow, OptionContract } from "../types";

const DAY_MS = 24 * 60 * 60 * 1000;

export function parseOptionSymbol(symbol: string) {
  const parts = symbol.split("-");
  if (parts.length !== 4) return null;

  const [underlying, datePart, strikePart, sidePart] = parts;
  const strike = Number(strikePart);
  if (!underlying || !datePart || !Number.isFinite(strike)) return null;
  if (sidePart !== "P" && sidePart !== "C") return null;

  return {
    underlying,
    datePart,
    strike,
    side: sidePart === "P" ? ("PUT" as const) : ("CALL" as const)
  };
}

export function daysToExpiry(expiryMs: number, nowMs: number) {
  return Math.max(0, (expiryMs - nowMs) / DAY_MS);
}

export function annualizedYield(premium: number, denominator: number, days: number) {
  if (denominator <= 0 || days <= 0) return null;
  return (premium / denominator) * (365 / days) * 100;
}

export function shortPutNetPremium(premium: number, strike: number, indexPrice: number | null | undefined) {
  if (!Number.isFinite(premium)) return null;
  if (indexPrice === null || indexPrice === undefined || !Number.isFinite(indexPrice)) return premium;

  const intrinsicLoss = Math.max(0, strike - indexPrice);
  return premium - intrinsicLoss;
}

export function selectNearbyStrikes(strikes: number[], referencePrice: number, count: number) {
  if (!Number.isFinite(referencePrice) || referencePrice <= 0) return [];
  const unique = Array.from(new Set(strikes)).sort((a, b) => a - b);
  const validCount = Math.max(1, Math.floor(count));

  return unique
    .map((strike) => ({ strike, distance: Math.abs(strike - referencePrice) }))
    .sort((a, b) => a.distance - b.distance || a.strike - b.strike)
    .slice(0, validCount)
    .map((item) => item.strike)
    .sort((a, b) => b - a);
}

export function filterStrikesByInterval(strikes: number[], interval: number) {
  if (!Number.isFinite(interval) || interval <= 0) return strikes;
  return strikes.filter((strike) => strike % interval === 0);
}

export function buildMatrix(
  contracts: OptionContract[],
  selectedStrikes: number[],
  marginPerContract: number,
  indexPrice?: number | null
): MatrixRow[] {
  const rows = new Map<number, MatrixRow>();

  for (const contract of contracts) {
    if (!selectedStrikes.includes(contract.strike)) continue;

    const row =
      rows.get(contract.expiry) ??
      {
        expiry: contract.expiry,
        expiryLabel: contract.expiryLabel,
        daysToExpiry: contract.daysToExpiry,
        cells: {}
      };

    const premium = contract.markPrice ?? 0;
    const netPremium = shortPutNetPremium(premium, contract.strike, indexPrice) ?? 0;
    row.cells[contract.strike] = {
      contract,
      premium,
      netPremium,
      actualYield: annualizedYield(netPremium, marginPerContract, contract.daysToExpiry),
      cashSecuredYield: annualizedYield(netPremium, contract.strike, contract.daysToExpiry)
    };
    rows.set(contract.expiry, row);
  }

  return Array.from(rows.values()).sort((a, b) => a.expiry - b.expiry);
}
