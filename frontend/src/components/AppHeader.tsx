import { FlaskConical, Radio, ShieldCheck, ShieldAlert } from "lucide-react";
import { dateTime, shortDate } from "../format";
import type { ScanRun, StatusResponse } from "../types";

interface Props {
  status: StatusResponse;
  lastSuccessful: ScanRun | null;
}

export function AppHeader({ status, lastSuccessful }: Props) {
  return (
    <header className="app-header">
      <div className="brand">
        <div className="logo" aria-hidden>
          <svg viewBox="0 0 32 32" width="32" height="32">
            <rect width="32" height="32" rx="8" fill="currentColor" />
            <path d="M8 20l5-8 4 5 3-4 4 7" stroke="white" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div>
          <h1>Catalyst Screener</h1>
          <p className="tagline">
            Finds US-listed biotech companies with upcoming Phase 2/3 readouts or PDUFA decisions that meet your
            criteria. For research only; it places no trades.
          </p>
        </div>
      </div>
      <div className="header-meta">
        <ModeIndicator status={status} />
        <div className="meta-item">
          <span className="meta-label">Last successful scan</span>
          <span className="meta-value">{lastSuccessful ? dateTime(lastSuccessful.finished_at) : "None yet"}</span>
        </div>
        <div className="meta-item">
          <span className="meta-label">Latest completed session</span>
          <span className="meta-value">{shortDate(status.latest_completed_session)}</span>
        </div>
      </div>
    </header>
  );
}

function ModeIndicator({ status }: { status: StatusResponse }) {
  if (status.mode === "demo") {
    return (
      <div className="mode mode-demo" title="Synthetic responses in the documented provider schemas, plus mock financials. Fictional companies.">
        <FlaskConical aria-hidden size={16} />
        <div>
          <span className="mode-title">Demo data</span>
          <span className="mode-sub">Synthetic fixtures · fictional companies</span>
        </div>
      </div>
    );
  }
  const verified = status.diagnostics?.ok;
  return (
    <div className="mode mode-live">
      <span className="live-dot" aria-hidden>
        <Radio size={16} />
      </span>
      <div>
        <span className="mode-title">Live data</span>
        <span className="mode-sub">
          {verified ? (
            <>
              <ShieldCheck aria-hidden size={12} /> Connection verified {dateTime(status.diagnostics!.ran_at)}
            </>
          ) : (
            <>
              <ShieldAlert aria-hidden size={12} /> Unverified. Run <code>python -m app.diagnostics</code>
            </>
          )}
        </span>
      </div>
    </div>
  );
}
