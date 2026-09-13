import { Play, RefreshCw, ServerCrash } from "lucide-react";
import type { DataMode } from "../types";

export function LoadingScreen() {
  return (
    <div className="center-screen" role="status" aria-live="polite">
      <div className="loader" aria-hidden />
      <p>Loading screener…</p>
    </div>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="center-screen">
      <div className="panel state-card" role="alert">
        <ServerCrash aria-hidden size={28} className="state-icon state-icon-error" />
        <h1>Backend unavailable</h1>
        <p>{message}</p>
        <p className="hint">
          Start it with <code>uvicorn app.main:app --port 8000</code> from the <code>backend</code> folder.
        </p>
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          <RefreshCw aria-hidden size={16} /> Retry
        </button>
      </div>
    </div>
  );
}

export function EmptyState({ mode }: { mode: DataMode }) {
  return (
    <div className="panel state-card">
      <Play aria-hidden size={28} className="state-icon" />
      <h2>No scans yet</h2>
      <p>
        Adjust the criteria if needed, then select <strong>Run scan</strong>.{" "}
        {mode === "demo"
          ? "Demo mode works without API keys."
          : "Live scans use your BPIQ and Alpaca credentials from the backend."}
      </p>
    </div>
  );
}

export function ResultsSkeleton() {
  return (
    <div aria-hidden className="skeleton-wrap">
      <div className="summary-grid">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="summary-card skeleton" />
        ))}
      </div>
      <div className="panel">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="skeleton skeleton-row" />
        ))}
      </div>
    </div>
  );
}
