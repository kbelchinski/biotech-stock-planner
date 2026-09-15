// Mirrors app/domain/research.py and the research API payloads.
import type { CatalystType, CompanyResult, CriterionStatus, ScanCriteria, SourceRef } from "./types";

export type TrackedStatus = "active" | "not_returned" | "date_passed";
export type TradeKind = "planned" | "hypothetical" | "actual" | "paper";
export type TradeStatus = "planned" | "pending_entry" | "open" | "closed" | "price_unavailable" | "cancelled";
export type MetricStatus = "ok" | "insufficient_history" | "missing_bars" | "unavailable";

export interface OutcomeRecord {
  provider_record_id: number;
  catalyst_date: string | null;
  stage: string | null;
  drug_name: string | null;
  text: string | null;
  detailed_text: string | null;
  source_url: string | null;
  news_published_at: string | null;
  open_price_gap_percent: number | null;
  intra_day_price_change_percent: number | null;
  provider_created_at: string | null;
  provider_updated_at: string | null;
  match_basis: string;
  source: SourceRef;
}

export interface TrackedCatalyst {
  mode: string;
  event_id: string;
  provider_record_id: number;
  ticker: string;
  drug_name: string | null;
  indications: string[];
  stage_label: string | null;
  event_label: string | null;
  stage_event_label: string | null;
  catalyst_type: CatalystType | null;
  classification_status: string;
  classification_reason: string;
  catalyst_date: string | null;
  date_precision: "provider_date" | "range" | "undated";
  range_start: string | null;
  range_end: string | null;
  note: string | null;
  source_url: string | null;
  provider_flags: Record<string, boolean | null>;
  status: TrackedStatus;
  first_seen_at: string;
  last_seen_at: string;
  not_returned_since: string | null;
  outcome: OutcomeRecord | null;
  source: SourceRef;
}

export interface CatalystRevision {
  event_id: string;
  ticker: string;
  observed_at: string;
  kind: string;
  field: string | null;
  previous: string | null;
  current: string | null;
}

export interface WatchItemView {
  mode: string;
  ticker: string;
  name: string | null;
  added_at: string;
  note: string | null;
  last_refresh_at: string | null;
  last_refresh_ok_at: string | null;
  last_refresh_error: string | null;
  catalyst_count: number;
  next_catalyst: TrackedCatalyst | null;
  undated_count: number;
  not_returned_count: number;
  date_revision_count: number;
  catalysts: TrackedCatalyst[];
}

export interface Notification {
  id: string;
  created_at: string;
  ticker: string;
  event_id: string | null;
  kind: string;
  title: string;
  body: string;
  read: boolean;
}

export interface ReminderRule {
  offset: number;
  unit: "calendar" | "trading";
}

export interface MonitoringSettings {
  reminder_rules: ReminderRule[];
  daily_refresh_enabled: boolean;
  daily_refresh_time_ny: string;
  benchmark_symbol: string;
}

export interface MonitoringStatus {
  process_scheduler_enabled: boolean;
  daily_refresh_enabled: boolean;
  daily_refresh_time_ny: string;
  next_scheduled_at: string;
  last_run: { scheduled_for: string; ran_at: string; ok: boolean } | null;
  running: boolean;
  last_error: string | null;
  behaviour: string;
}

export interface RefreshJob {
  id: string;
  status: "running" | "completed" | "failed";
  message: string;
  report: {
    tickers: Record<string, { ok: boolean; error?: string; catalysts?: number; revisions?: number; notifications?: number; issues?: string[] }>;
    issues: string[];
    notifications_created: number;
  } | null;
  error: string | null;
}

export interface Metric {
  key: string;
  label: string;
  value: number | null;
  unit: "pct" | "ratio" | "usd" | "shares" | "pct_points";
  status: MetricStatus;
  formula: string;
  lookback: string;
  detail: string | null;
}

export interface PriceContext {
  ticker: string;
  benchmark_symbol: string;
  feed: string;
  adjustment: string;
  latest_completed_session: string;
  latest_bar_session: string | null;
  expected_sessions: number;
  present_sessions: number;
  window_start: string;
  retrieved_at: string | null;
  freshness: string;
  metrics: Metric[];
  gaps: { session: string; previous_session: string; previous_close: number; open: number; gap_pct: number }[];
  gap_threshold_pct: number;
  first_seen: { basis: string; scan_id: string; scan_date: string; price_session: string | null } | null;
  conventions: string[];
  issues: string[];
  chart_bars: { session_date: string; open: number; high: number; low: number; close: number; volume: number }[];
}

export interface McpSection {
  state: "ok" | "empty" | "error" | "unavailable";
  reason: string | null;
  tool?: string;
  records: Record<string, unknown>[];
  raw_preview?: string;
}

export interface EvidenceItem {
  id: string;
  category: "supporting" | "against" | "missing_or_stale" | "changes" | "invalidation_conditions" | "context";
  kind: "fact" | "calculation" | "user_assumption";
  text: string;
  source: Partial<SourceRef> | null;
}

export interface Research {
  ticker: string;
  mode: string;
  generated_at: string;
  today: string;
  latest_completed_session: string;
  overview: {
    name: string | null;
    exchange: string | null;
    market_cap_usd: number | null;
    market_cap_source: SourceRef | null;
    provider_last_price: string | null;
    watched: boolean;
  };
  screening: {
    result: CompanyResult | null;
    scan: { id: string; outcome: string; scan_date: string; finished_at: string; rule_version: string; latest_completed_session: string | null; criteria: ScanCriteria } | null;
    note: string;
  };
  scan_history: { scan_id: string; eligibility: string; scan_date: string; price: number | null; finished_at: string }[];
  catalysts: {
    origin: "tracked" | "live_query" | null;
    retrieved_at?: string | null;
    covered_until?: string | null;
    items: TrackedCatalyst[];
    revisions: CatalystRevision[];
    historical: OutcomeRecord[];
    issues: string[];
    primary_event_id: string | null;
    earlier_than_primary: string[];
  };
  price_context: PriceContext | null;
  price_context_error: string | null;
  financials: { mcp: McpSection; screening_runway: { status: CriterionStatus; observed: string | null; explanation: string; is_mock: boolean } | null };
  insiders: { mcp: McpSection };
  funds: { mcp: McpSection; provider_flags: ({ event_id: string } & Record<string, boolean | null | string>)[] };
  questions: { id: string; question: string; answer: string; kind: string; known: boolean }[];
  evidence: EvidenceItem[];
  changes: { previous_saved_at: string; items: { key: string; previous: string | null; current: string | null }[] } | null;
  issues: string[];
}

export interface TradePlanInput {
  ticker: string;
  entry_price: number | null;
  planned_exit_date: string | null;
  thesis: string;
  invalidation: string;
  loss_budget_usd: number | null;
  stop_price: number | null;
  position_shares: number | null;
  capital_allocation_usd: number | null;
  portfolio_value_usd: number | null;
  decline_scenarios_pct: number[];
}

export interface TradePlanResult {
  output: {
    valid: boolean;
    errors: string[];
    warnings: string[];
    capital_commitment_usd: number | null;
    capital_basis: string | null;
    implied_shares: number | null;
    stop_based_shares: number | null;
    stop_based_capital_usd: number | null;
    stop_based_note: string | null;
    risk_per_share_usd: number | null;
    portfolio_exposure_pct: number | null;
    loss_scenarios: { decline_pct: number; loss_usd: number | null; portfolio_impact_pct: number | null }[];
    disclaimer: string;
  };
  catalysts_before_exit: (TrackedCatalyst & { relation: string })[];
  catalyst_note: string | null;
}

export interface Trade {
  id: string;
  kind: TradeKind;
  status: TradeStatus;
  ticker: string;
  created_at: string;
  updated_at: string;
  plan: TradePlanInput | null;
  catalyst_event_id: string | null;
  catalyst_category: string | null;
  source_scan_id: string | null;
  entry_date: string | null;
  entry_price: number | null;
  entry_basis: string | null;
  planned_exit_date: string | null;
  exit_date: string | null;
  exit_price: number | null;
  exit_basis: string | null;
  shares: number | null;
  notional_usd: number | null;
  cost_pct_per_side: number;
  slippage_pct_per_side: number;
  thesis: string;
  invalidation: string;
  notes: string;
  history: string[];
}

export interface JournalEntry {
  id: string;
  created_at: string;
  ticker: string;
  decision: "enter" | "skip" | "exit" | "note";
  trade_id: string | null;
  thesis: string;
  expected_catalyst: string;
  reasons: string;
  result_note: string;
}

export interface OutcomeRow {
  trade_id: string;
  ticker: string;
  category: string;
  entry_date: string;
  exit_date: string;
  net_return: number;
  benchmark_return: number | null;
  relative_return: number | null;
}

export interface PerformanceSummary {
  kind: TradeKind;
  closed_count: number;
  open_count: number;
  unresolved_count: number;
  observation_start: string | null;
  observation_end: string | null;
  win_rate: number | null;
  average_win: number | null;
  average_loss: number | null;
  total_compounded_return: number | null;
  max_drawdown: number | null;
  worst: OutcomeRow[];
  benchmark_relative_average: number | null;
  benchmark_coverage: number;
  by_category: { category: string; count: number; win_rate: number | null; average_net_return: number | null }[];
  open_positions: { trade_id: string; ticker: string; entry_date: string; entry_price: number; planned_exit_date: string | null; mark_date: string | null; mark_close: number | null; unrealized_net_return: number | null }[];
  conventions: string[];
  warnings: string[];
}

export interface McpTool {
  name: string;
  title: string | null;
  description: string | null;
  input_schema: Record<string, unknown> | null;
  output_schema: Record<string, unknown> | null;
}

export interface McpStatus {
  configured: boolean;
  connected: boolean;
  auth_method?: "api_key" | "oauth" | null;
  message?: string;
  url?: string;
  obtained_at?: number | null;
  expires_at?: number | null;
  discovered_at?: string | null;
  tools?: McpTool[];
  suggestions?: Record<string, string[]>;
  mapping?: Record<string, { tool: string | null; confirmed: boolean; argument?: string | null }>;
  verification_note?: string;
  demo_note?: string;
}

export interface IntegrationsStatus {
  mode: string;
  bpiq_rest: { configured: boolean; access_tier: string };
  alpaca: { configured: boolean; plan: string };
  bpiq_mcp: McpStatus;
  ai: { provider: string; enabled: boolean; model: string | null; problems: string[]; monthly_budget_usd: number; month: string; month_spend_usd: number; note: string };
  monitoring: MonitoringStatus;
  orders: string;
}

export interface CritiqueResponse {
  available: boolean;
  reason?: string;
  evidence: EvidenceItem[];
  sections?: Record<string, { point: string; evidence_ids: string[] }[]>;
  removed?: { uncited: number; forbidden: number };
  label?: string;
  model?: string;
  generated_at?: string;
  estimated_cost_usd?: number;
}

export interface AskResponse {
  available: boolean;
  reason?: string;
  question?: string;
  answer?: string;
  sources?: { url: string; title: string }[];
  web_search_enabled?: boolean;
  web_searches?: number;
  search_queries?: string[];
  removed_sentences?: number;
  model?: string;
  generated_at?: string;
  estimated_cost_usd?: number;
  label?: string;
}
