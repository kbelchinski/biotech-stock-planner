import type {
  CatalystType,
  CompanyResult,
  CatalystEvaluation,
  CriterionKey,
  CriterionStatus,
  Eligibility,
} from "./types";

export const CATALYST_TYPE_LABEL: Record<CatalystType, string> = {
  phase2_results: "Phase 2 results",
  phase3_results: "Phase 3 results",
  pdufa: "PDUFA decision",
};

export const CRITERION_LABEL: Record<CriterionKey, string> = {
  catalyst: "Catalyst",
  market_cap: "Market cap",
  price: "Price",
  runway: "Runway",
  liquidity: "Liquidity",
  listing: "Listing",
};

export const STATUS_LABEL: Record<CriterionStatus, string> = {
  pass: "Pass",
  fail: "Fail",
  unknown: "Unknown",
  not_applied: "Not applied",
};

export const ELIGIBILITY_LABEL: Record<Eligibility, string> = {
  qualifies: "Qualifies",
  does_not_qualify: "Does not qualify",
  insufficient_data: "Insufficient data",
};

export function usd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(2)}`;
}

export function price(value: number | null | undefined): string {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}

/** Formats a YYYY-MM-DD calendar date without shifting it through the local timezone. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + (Number.isFinite(days) ? days : 0)));
  return date.toISOString().slice(0, 10);
}

export function primaryCatalyst(result: CompanyResult): CatalystEvaluation | null {
  return (
    result.catalysts.find((e) => e.catalyst.event_id === result.primary_catalyst_event_id) ??
    result.catalysts[0] ??
    null
  );
}

export function catalystLabel(evaluation: CatalystEvaluation): string {
  const c = evaluation.catalyst;
  if (c.catalyst_type) return CATALYST_TYPE_LABEL[c.catalyst_type];
  return c.stage_event_label || [c.stage_label, c.event_label].filter(Boolean).join(" · ") || "Unlabelled";
}
