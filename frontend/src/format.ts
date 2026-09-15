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

/** Fraction → percentage text, e.g. 0.123 → "+12.3%". */
export function pct(value: number | null | undefined, digits = 1, signed = true): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const v = value * 100;
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function metricValue(metric: { value: number | null; unit: string }): string {
  const v = metric.value;
  if (v == null || !Number.isFinite(v)) return "—";
  switch (metric.unit) {
    case "ratio":
      return `${v.toFixed(2)}×`;
    case "pct":
      return pct(v);
    case "pct_points":
      return `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)} pp`;
    case "usd":
      return usd(v);
    default:
      return v.toLocaleString("en-US");
  }
}

/** Calendar days from `fromIso` to `toIso` (YYYY-MM-DD), or null. */
export function daysBetween(fromIso: string, toIso: string | null | undefined): number | null {
  if (!toIso) return null;
  const utc = (iso: string) => {
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((utc(toIso) - utc(fromIso)) / 86_400_000);
}

export function trackedLabel(c: { catalyst_type: CatalystType | null; stage_event_label: string | null; stage_label: string | null; event_label: string | null }): string {
  if (c.catalyst_type) return CATALYST_TYPE_LABEL[c.catalyst_type];
  return c.stage_event_label || [c.stage_label, c.event_label].filter(Boolean).join(" · ") || "Unlabelled";
}

export function parseNumber(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : NaN;
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
