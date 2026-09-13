// Mirrors the backend's normalized models (app/domain/models.py) as serialized to JSON.

export type DataMode = "demo" | "live";
export type CriterionStatus = "pass" | "fail" | "unknown" | "not_applied";
export type Eligibility = "qualifies" | "does_not_qualify" | "insufficient_data";
export type CatalystType = "phase2_results" | "phase3_results" | "pdufa";
export type CriterionKey = "catalyst" | "market_cap" | "price" | "runway" | "liquidity" | "listing";
export type ClassificationStatus = "matched" | "not_qualifying" | "unrecognized";
export type ScanOutcome =
  | "success"
  | "no_matches"
  | "incomplete"
  | "provider_unavailable"
  | "subscription_limitation"
  | "configuration_error";

export interface SourceRef {
  provider: string;
  endpoint: string | null;
  retrieved_at: string | null;
  source_timestamp: string | null;
  timestamp_note: string | null;
  url: string | null;
  is_mock: boolean;
}

export interface Catalyst {
  event_id: string;
  provider_record_id: number;
  ticker: string;
  company_name: string | null;
  drug_name: string | null;
  indications: string[];
  stage_label: string | null;
  event_label: string | null;
  stage_event_label: string | null;
  catalyst_type: CatalystType | null;
  classification_status: ClassificationStatus;
  classification_reason: string;
  catalyst_date: string | null;
  note: string | null;
  source_url: string | null;
  source: SourceRef;
}

export interface CatalystEvaluation {
  catalyst: Catalyst;
  days_until: number | null;
  type_status: CriterionStatus;
  timing_status: CriterionStatus;
  status: CriterionStatus;
}

export interface WeeklyTurnover {
  week_start: string;
  week_end: string;
  sessions: string[];
  sessions_with_bars: number;
  total_usd: number;
  complete: boolean;
  approximated_sessions: number;
}

export interface CriterionResult {
  key: CriterionKey;
  label: string;
  status: CriterionStatus;
  observed: string | null;
  threshold: string;
  explanation: string;
  sources: SourceRef[];
  details: {
    weeks?: WeeklyTurnover[];
    method?: string;
    missing_sessions?: string[];
    [key: string]: unknown;
  };
}

export interface CompanyResult {
  ticker: string;
  name: string | null;
  eligibility: Eligibility;
  criteria: CriterionResult[];
  catalysts: CatalystEvaluation[];
  primary_catalyst_event_id: string | null;
  market_cap_usd: number | null;
  price: number | null;
  price_date: string | null;
  runway_months: number | null;
  runway_is_mock: boolean;
  avg_weekly_turnover_usd: number | null;
  turnover_is_lower_bound: boolean;
  turnover_method: string | null;
  exchange: string | null;
  issues: string[];
}

export interface ScanCriteria {
  catalyst_min_days: number;
  catalyst_max_days: number;
  catalyst_types: CatalystType[];
  market_cap_enabled: boolean;
  market_cap_min_usd: number;
  market_cap_max_usd: number;
  price_enabled: boolean;
  price_above_usd: number;
  runway_enabled: boolean;
  runway_min_months: number;
  liquidity_enabled: boolean;
  liquidity_min_avg_weekly_usd: number;
  listing_enabled: boolean;
  include_near_term_catalysts: boolean;
  provider_market_cap_prefilter: boolean;
}

export interface ScanIssue {
  severity: "error" | "warning" | "info";
  provider: string | null;
  kind: string;
  message: string;
  hint: string | null;
  tickers: string[];
}

export interface ScanSummary {
  evaluated: number;
  qualifying: number;
  failed: number;
  insufficient_data: number;
  catalyst_records: number;
  undated_excluded: number;
  rejected_records: number;
  duplicate_records: number;
}

export interface ScanRun {
  id: string;
  mode: DataMode;
  outcome: ScanOutcome;
  started_at: string;
  finished_at: string;
  scan_date: string;
  latest_completed_session: string | null;
  criteria: ScanCriteria;
  summary: ScanSummary;
  results: CompanyResult[];
  issues: ScanIssue[];
  not_applied_criteria: CriterionKey[];
  notices: string[];
  demo_scenario: string | null;
}

export interface StatusResponse {
  mode: DataMode;
  scan_date: string;
  latest_completed_session: string;
  live_configuration_problems: string[];
  providers: {
    bpiq: { name: string; configured: boolean; access_tier: string; rate_limit_per_min: number; trial_horizon_days: number };
    alpaca: { name: string; configured: boolean; account_type: string; feed: string; adjustment: string };
  };
  runway: { available: boolean; source: string | null; note: string | null };
  diagnostics: { ran_at: string; ok: boolean } | null;
  defaults: ScanCriteria;
  catalyst_types: { id: CatalystType; label: string }[];
  demo_scenarios: { id: string; label: string; description: string }[];
}

export interface ScanJob {
  id: string;
  status: "running" | "completed" | "failed";
  message: string;
  step: number;
  total_steps: number;
  scan_id: string | null;
  error: string | null;
  started_at: string;
}

export interface LatestResponse {
  latest_successful: ScanRun | null;
  latest_attempt: ScanRun | null;
}
