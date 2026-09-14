import { useEffect, useRef } from "react";
import { ExternalLink, FlaskConical, X } from "lucide-react";
import { PriceChart } from "./PriceChart";
import { CriterionBadge, EligibilityBadge } from "./StatusBadge";
import { catalystLabel, dateTime, shortDate, usd } from "../format";
import type { CatalystEvaluation, CompanyResult, CriterionResult, ScanRun, SourceRef } from "../types";

interface Props {
  result: CompanyResult;
  run: ScanRun;
  onClose: () => void;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function CompanyDrawer({ result, run, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
      } else if (event.key === "Tab" && panelRef.current) {
        const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("no-scroll");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("no-scroll");
      previouslyFocused?.focus();
    };
  }, []);

  const titleId = `drawer-title-${result.ticker}`;
  const visibleCriteria = result.criteria.filter((c) => c.key !== "runway" || c.status !== "not_applied");
  const notApplied = visibleCriteria.filter((c) => c.status === "not_applied");

  return (
    <div className="drawer-root">
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <div className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panelRef}>
        <header className="drawer-header">
          <div>
            <div className="drawer-title-row">
              <h2 id={titleId}>{result.ticker}</h2>
              {result.exchange && <span className="tag">{result.exchange}</span>}
              {run.mode === "demo" && (
                <span className="tag tag-mock">
                  <FlaskConical aria-hidden size={11} /> Demo
                </span>
              )}
            </div>
            <p className="drawer-subtitle">{result.name ?? "Company name unavailable"}</p>
          </div>
          <button ref={closeRef} type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close details">
            <X aria-hidden size={18} />
          </button>
        </header>

        <div className="drawer-body">
          <div className="drawer-verdict">
            <EligibilityBadge value={result.eligibility} />
            <span>
              {result.eligibility === "qualifies" && "Every enabled criterion passes."}
              {result.eligibility === "does_not_qualify" && "At least one enabled criterion fails."}
              {result.eligibility === "insufficient_data" && "No enabled criterion fails, but at least one is unknown."}
            </span>
          </div>
          {notApplied.length > 0 && (
            <p className="callout callout-warn">
              Not applied in this scan: {notApplied.map((c) => c.label).join(", ")}. The result does not cover these criteria.
            </p>
          )}
          {result.runway_is_mock && result.criteria.some((c) => c.key === "runway" && c.status !== "not_applied") && (
            <p className="callout callout-mock">Cash runway uses demo mock financials. They don't come from any provider.</p>
          )}
          {result.issues.length > 0 && (
            <ul className="callout callout-warn">
              {result.issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}

          <PriceChart ticker={result.ticker} bars={result.price_bars ?? []} />

          <h3 className="section-title">Criteria</h3>
          <div className="criteria-list">
            {visibleCriteria.map((criterion) => (
              <CriterionCard key={criterion.key} criterion={criterion} />
            ))}
          </div>

          <h3 className="section-title">Catalysts ({result.catalysts.length})</h3>
          <ol className="catalyst-list">
            {result.catalysts.map((evaluation) => (
              <CatalystItem key={evaluation.catalyst.event_id} evaluation={evaluation} primary={evaluation.catalyst.event_id === result.primary_catalyst_event_id} />
            ))}
          </ol>

          <p className="drawer-foot">
            Scan {run.id.slice(0, 8)} · {run.mode} mode · scan date {shortDate(run.scan_date)} · finished {dateTime(run.finished_at)}
          </p>
        </div>
      </div>
    </div>
  );
}

function CriterionCard({ criterion }: { criterion: CriterionResult }) {
  const weeks = criterion.details.weeks;
  return (
    <article className={`criterion criterion-${criterion.status}`}>
      <header>
        <h4>{criterion.label}</h4>
        <CriterionBadge status={criterion.status} />
      </header>
      <dl className="kv">
        <div>
          <dt>Observed</dt>
          <dd>{criterion.observed ?? "—"}</dd>
        </div>
        <div>
          <dt>Threshold</dt>
          <dd>{criterion.threshold}</dd>
        </div>
      </dl>
      <p className="explanation">{criterion.explanation}</p>
      {weeks && weeks.length > 0 && (
        <div className="mini-table-wrap">
          <table className="mini-table">
            <caption className="sr-only">Weekly dollar turnover</caption>
            <thead>
              <tr>
                <th scope="col">Week</th>
                <th scope="col" className="num">Sessions with bars</th>
                <th scope="col" className="num">Turnover</th>
              </tr>
            </thead>
            <tbody>
              {weeks.map((week) => (
                <tr key={week.week_start}>
                  <td>
                    {shortDate(week.sessions[0])} – {shortDate(week.sessions[week.sessions.length - 1])}
                  </td>
                  <td className="num">
                    {week.sessions_with_bars}/{week.sessions.length}
                    {!week.complete && <span className="tag tag-warn">incomplete</span>}
                  </td>
                  <td className="num">{usd(week.total_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {criterion.sources.length > 0 && <Sources sources={criterion.sources} />}
    </article>
  );
}

function Sources({ sources }: { sources: SourceRef[] }) {
  return (
    <ul className="sources">
      {sources.map((source, index) => (
        <li key={index}>
          <span className="source-provider">
            {source.provider}
            {source.is_mock && <span className="tag tag-mock">Mock</span>}
          </span>
          {source.endpoint && <code className="source-endpoint">{source.endpoint}</code>}
          <span className="source-time">
            {source.source_timestamp
              ? `Source time ${source.source_timestamp.length > 10 ? dateTime(source.source_timestamp) : shortDate(source.source_timestamp)}`
              : source.retrieved_at
                ? `Retrieved ${dateTime(source.retrieved_at)}`
                : "No timestamp"}
            {source.timestamp_note && ` · ${source.timestamp_note}`}
          </span>
          {source.url && (
            <a href={source.url} target="_blank" rel="noreferrer noopener" className="link">
              Source <ExternalLink aria-hidden size={12} />
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

function CatalystItem({ evaluation, primary }: { evaluation: CatalystEvaluation; primary: boolean }) {
  const c = evaluation.catalyst;
  return (
    <li className={`catalyst${primary ? " is-primary" : ""}`}>
      <div className="catalyst-head">
        <span className="catalyst-type">{catalystLabel(evaluation)}</span>
        {primary && <span className="tag">Primary</span>}
        <CriterionBadge status={evaluation.status} size="sm" />
      </div>
      <p className="catalyst-when">
        {shortDate(c.catalyst_date)}
        {evaluation.days_until != null && ` · ${evaluation.days_until} days away`}
      </p>
      <div className="catalyst-checks">
        <span>
          Type <CriterionBadge status={evaluation.type_status} size="sm" />
        </span>
        <span>
          Timing <CriterionBadge status={evaluation.timing_status} size="sm" />
        </span>
      </div>
      <dl className="kv kv-compact">
        {c.drug_name && (
          <div>
            <dt>Drug</dt>
            <dd>{c.drug_name}</dd>
          </div>
        )}
        {c.indications.length > 0 && (
          <div>
            <dt>Indication</dt>
            <dd>{c.indications.join(", ")}</dd>
          </div>
        )}
        <div>
          <dt>BPIQ stage / event</dt>
          <dd>
            {c.stage_label ?? "—"} / {c.event_label ?? "—"}
          </dd>
        </div>
        <div>
          <dt>Classification</dt>
          <dd>{c.classification_reason}</dd>
        </div>
        <div>
          <dt>Event ID</dt>
          <dd>
            <code>{c.event_id}</code>
          </dd>
        </div>
      </dl>
      {c.note && <p className="catalyst-note">{c.note}</p>}
      {c.source_url && (
        <a href={c.source_url} target="_blank" rel="noreferrer noopener" className="link">
          Catalyst source <ExternalLink aria-hidden size={12} />
        </a>
      )}
    </li>
  );
}
