import type { LatestResponse, ScanCriteria, ScanJob, ScanRun, StatusResponse } from "./types";

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
