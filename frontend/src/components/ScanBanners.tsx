import { useId } from "react";
import { CircleAlert, CircleCheck, Info, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react";
import { CRITERION_LABEL, dateTime } from "../format";
import type { ScanOutcome, ScanRun } from "../types";

type Tone = "error" | "warn" | "info" | "success";

const OUTCOME: Record<ScanOutcome, { title: string; tone: Tone; icon: LucideIcon }> = {
  success: { title: "Scan completed", tone: "success", icon: CircleCheck },
  no_matches: { title: "Scan completed: no company met every enabled criterion", tone: "info", icon: Info },
  incomplete: { title: "Scan incomplete: some data could not be retrieved", tone: "warn", icon: TriangleAlert },
  provider_unavailable: { title: "Provider unavailable: the scan could not run", tone: "error", icon: OctagonAlert },
  subscription_limitation: { title: "Subscription limitation", tone: "error", icon: CircleAlert },
  configuration_error: { title: "Configuration problem", tone: "error", icon: CircleAlert },
};

interface Props {
  attempt: ScanRun | null;
  lastSuccessful: ScanRun | null;
  displayed: ScanRun | null;
  showingAttempt: boolean;
  onViewAttempt: (value: boolean) => void;
}

export function ScanBanners({ attempt, lastSuccessful, displayed, showingAttempt, onViewAttempt }: Props) {
  return (
    <div className="banners">
      {attempt && (
        <AttemptBanner attempt={attempt} lastSuccessful={lastSuccessful} showingAttempt={showingAttempt} onViewAttempt={onViewAttempt} />
      )}
      {displayed && displayed.not_applied_criteria.length > 0 && (
        <div className="alert alert-warn" role="note">
          <TriangleAlert aria-hidden size={18} />
          <div>
            <strong>Not applied: {displayed.not_applied_criteria.map((k) => CRITERION_LABEL[k]).join(", ")}.</strong>{" "}
            "Qualifies" in this scan does not cover the full screening strategy.
          </div>
        </div>
      )}
      {displayed && displayed.outcome === "no_matches" && !showingAttempt && (
        <div className="alert alert-info" role="status">
          <Info aria-hidden size={18} />
          <div>
            <strong>{OUTCOME.no_matches.title}.</strong>{" "}
            {displayed.results.length === 0
              ? "BPIQ returned no dated catalysts in the query range."
              : "Review Insufficient data results for companies that might qualify with more data."}
          </div>
        </div>
      )}
      {displayed && displayed.notices.length > 0 && <Notices run={displayed} />}
    </div>
  );
}

function AttemptBanner({
  attempt,
  lastSuccessful,
  showingAttempt,
  onViewAttempt,
}: {
  attempt: ScanRun;
  lastSuccessful: ScanRun | null;
  showingAttempt: boolean;
  onViewAttempt: (value: boolean) => void;
}) {
  const meta = OUTCOME[attempt.outcome];
  const Icon = meta.icon;
  const hasPartialResults = attempt.results.length > 0;
  return (
    <div className={`alert alert-${meta.tone}`} role="alert">
      <Icon aria-hidden size={18} />
      <div className="alert-body">
        <strong>{meta.title}</strong>
        <span className="alert-meta">Attempted {dateTime(attempt.finished_at)}</span>
        <ul className="issue-list">
          {attempt.issues.map((issue, index) => (
            <li key={index}>
              {issue.provider && <span className="issue-provider">{issue.provider}</span>}
              {issue.message}
              {issue.hint && <span className="issue-hint">{issue.hint}</span>}
              {issue.tickers.length > 0 && issue.tickers.length <= 12 && (
                <span className="issue-tickers">Affected: {issue.tickers.join(", ")}</span>
              )}
            </li>
          ))}
        </ul>
        <div className="alert-actions">
          {hasPartialResults ? (
            showingAttempt ? (
              <>
                <span>Showing partial results from this scan.</span>
                {lastSuccessful && (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => onViewAttempt(false)}>
                    View last successful scan ({dateTime(lastSuccessful.finished_at)})
                  </button>
                )}
              </>
            ) : (
              <>
                <span>Showing the last successful scan.</span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => onViewAttempt(true)}>
                  View partial results
                </button>
              </>
            )
          ) : lastSuccessful ? (
            <span>Showing the previous successful scan from {dateTime(lastSuccessful.finished_at)}.</span>
          ) : (
            <span>No previous successful scan to show.</span>
          )}
        </div>
      </div>
    </div>
  );
}

function Notices({ run }: { run: ScanRun }) {
  const id = useId();
  return (
    <details className="notices">
      <summary id={id}>
        <Info aria-hidden size={14} /> Scan notes ({run.notices.length})
      </summary>
      <ul aria-labelledby={id}>
        {run.notices.map((notice, index) => (
          <li key={index}>{notice}</li>
        ))}
      </ul>
    </details>
  );
}
