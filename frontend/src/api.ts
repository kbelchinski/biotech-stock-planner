import type { LatestResponse, ScanCriteria, ScanJob, ScanRun, StatusResponse } from "./types";
import type {
  AskResponse,
  CritiqueResponse,
  IntegrationsStatus,
  JournalEntry,
  McpStatus,
  MonitoringSettings,
  MonitoringStatus,
  Notification,
  PerformanceSummary,
  RefreshJob,
  Research,
  Trade,
  TradePlanInput,
  TradePlanResult,
  WatchItemView,
} from "./researchTypes";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw new ApiError("Cannot reach the backend. Is it running on port 8000?", 0);
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      // keep the status text
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });
const scenarioQuery = (scenario: string | null) => (scenario ? `?demo_scenario=${encodeURIComponent(scenario)}` : "");

export const researchApi = {
  watchlist: () => request<{ mode: string; items: WatchItemView[]; monitoring: MonitoringStatus }>("/api/watchlist"),
  watch: (ticker: string, note?: string, name?: string | null) => request<unknown>("/api/watchlist", json("POST", { ticker, note, name })),
  unwatch: (ticker: string) => request<unknown>(`/api/watchlist/${encodeURIComponent(ticker)}`, json("DELETE")),
  startRefresh: (scenario: string | null, tickers?: string[]) =>
    request<RefreshJob>("/api/watchlist/refresh", json("POST", { demo_scenario: scenario, tickers })),
  refreshJob: (id: string) => request<RefreshJob>(`/api/watchlist/refresh/${id}`),
  notifications: () => request<{ items: Notification[]; unread: number }>("/api/notifications"),
  markRead: (ids: string[] | null) => request<{ unread: number }>("/api/notifications/read", json("POST", { ids })),
  monitoring: () => request<{ settings: MonitoringSettings; status: MonitoringStatus }>("/api/monitoring"),
  saveMonitoring: (settings: MonitoringSettings) =>
    request<{ settings: MonitoringSettings; status: MonitoringStatus }>("/api/monitoring/settings", json("PUT", settings)),
  research: (ticker: string, scenario: string | null) =>
    request<Research>(`/api/companies/${encodeURIComponent(ticker)}/research${scenarioQuery(scenario)}`),
  saveAnalysis: (ticker: string, note: string, scenario: string | null) =>
    request<{ id: number; created_at: string }>(`/api/companies/${encodeURIComponent(ticker)}/analyses`, json("POST", { note, demo_scenario: scenario })),
  critique: (ticker: string, invalidation: string, scenario: string | null) =>
    request<CritiqueResponse>(`/api/companies/${encodeURIComponent(ticker)}/critique`, json("POST", { invalidation, demo_scenario: scenario })),
  ask: (ticker: string, question: string, scenario: string | null) =>
    request<AskResponse>(`/api/companies/${encodeURIComponent(ticker)}/ask`, json("POST", { question, demo_scenario: scenario })),
  calculatePlan: (plan: TradePlanInput, scenario: string | null) =>
    request<TradePlanResult>("/api/trade-plans/calculate", json("POST", { plan, demo_scenario: scenario })),
  trades: () => request<Trade[]>("/api/trades"),
  createTrade: (body: Record<string, unknown>) => request<Trade>("/api/trades", json("POST", body)),
  updateTrade: (id: string, body: Record<string, unknown>) => request<Trade>(`/api/trades/${id}`, json("PUT", body)),
  deleteTrade: (id: string) => request<unknown>(`/api/trades/${id}`, json("DELETE")),
  journal: () => request<JournalEntry[]>("/api/journal"),
  addJournal: (body: Record<string, unknown>) => request<JournalEntry>("/api/journal", json("POST", body)),
  deleteJournal: (id: string) => request<unknown>(`/api/journal/${id}`, json("DELETE")),
  performance: (scenario: string | null) =>
    request<{ mode: string; benchmark: string; summaries: Record<"actual" | "hypothetical" | "paper", PerformanceSummary>; issues: string[] }>(
      `/api/performance${scenarioQuery(scenario)}`,
    ),
  scanHistory: () =>
    request<{ id: string; outcome: string; scan_date: string; finished_at: string; rule_version: string; issue_count: number; summary: { evaluated: number; qualifying: number } }[]>(
      "/api/scans?limit=50",
    ),
  integrations: () => request<IntegrationsStatus>("/api/integrations/status"),
  mcpConnect: () => request<{ authorize_url: string }>("/api/integrations/bpiq-mcp/connect", json("POST")),
  mcpDisconnect: () => request<McpStatus>("/api/integrations/bpiq-mcp/disconnect", json("POST")),
  mcpDiscover: () => request<McpStatus>("/api/integrations/bpiq-mcp/discover", json("POST")),
  mcpTestTool: (name: string, args: Record<string, unknown>) =>
    request<{ tool: string; retrieved_at: string; payload: unknown }>("/api/integrations/bpiq-mcp/test-tool", json("POST", { name, arguments: args })),
  mcpMapping: (mapping: Record<string, { tool: string | null; confirmed: boolean }>) =>
    request<McpStatus>("/api/integrations/bpiq-mcp/capabilities", json("PUT", mapping)),
};

export const api = {
  status: () => request<StatusResponse>("/api/status"),
  latest: () => request<LatestResponse>("/api/scans/latest"),
  startScan: (criteria: ScanCriteria, demoScenario: string | null) =>
    request<ScanJob>("/api/scans", {
      method: "POST",
      body: JSON.stringify({ criteria, demo_scenario: demoScenario }),
    }),
  job: (id: string) => request<ScanJob>(`/api/scans/jobs/${id}`),
  scan: (id: string) => request<ScanRun>(`/api/scans/${id}`),
  exportUrl: (id: string) => `/api/scans/${id}/export.csv`,
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
