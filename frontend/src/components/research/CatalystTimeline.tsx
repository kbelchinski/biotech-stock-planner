import { ExternalLink } from "lucide-react";
import { daysBetween, dateTime, shortDate, trackedLabel } from "../../format";
import type { CatalystRevision, OutcomeRecord, TrackedCatalyst } from "../../researchTypes";
import { Empty, KindBadge } from "./ui";

interface Props {
  items: TrackedCatalyst[];
  revisions: CatalystRevision[];
  today: string;
  primaryId?: string | null;
  earlierIds?: string[];
  historical?: OutcomeRecord[];
  /** Highlight events on or before this date (e.g. a planned exit). */
  horizon?: string | null;
}

const STATUS_TEXT: Record<TrackedCatalyst["status"], string> = {
  active: "Active",
  not_returned: "No longer returned",
  date_passed: "Date passed",
};

const REVISION_TEXT: Record<string, string> = {
  first_seen: "First seen",
  date_changed: "Date changed",
  type_changed: "Stage/event relabelled",
  note_changed: "Note changed",
  not_returned: "No longer returned by provider",
  returned_again: "Returned again",
  outcome_reported: "Possible outcome matched",
};

export function CatalystTimeline({ items, revisions, today, primaryId, earlierIds = [], historical = [], horizon }: Props) {
  if (items.length === 0 && historical.length === 0) {
    return <Empty title="No catalysts from connected sources">Missing catalysts are unknown, not evidence that none exist.</Empty>;
  }
  const byEvent = new Map<string, CatalystRevision[]>();
  revisions.forEach((r) => byEvent.set(r.event_id, [...(byEvent.get(r.event_id) ?? []), r]));

  return (
    <div className="timeline-wrap">
      {items.length > 0 && (
        <ol className="timeline">
          {items.map((c) => {
            const days = daysBetween(today, c.catalyst_date);
            const eventRevisions = (byEvent.get(c.event_id) ?? []).filter((r) => r.kind !== "first_seen");
            const earlier = earlierIds.includes(c.event_id);
            const beforeHorizon = horizon && c.catalyst_date && c.catalyst_date <= horizon;
            const flags = Object.entries(c.provider_flags ?? {}).filter(([, v]) => v === true);
            return (
              <li key={c.event_id} className={`tl-item tl-${c.status}${c.event_id === primaryId ? " is-primary" : ""}${earlier ? " is-earlier" : ""}`}>
                <div className="tl-date">
                  <span className="tl-day">{c.catalyst_date ? shortDate(c.catalyst_date) : "Undated"}</span>
                  <span className="tl-precision">
                    {c.date_precision === "undated"
                      ? "No date from provider"
                      : c.date_precision === "range"
                        ? `Range ${shortDate(c.range_start)} – ${shortDate(c.range_end)}`
                        : "Single provider date · precision not documented"}
                  </span>
                  {days != null && c.status === "active" && <span className="tl-days">{days >= 0 ? `in ${days} days` : `${-days} days ago`}</span>}
                </div>
                <div className="tl-body">
                  <div className="tl-title">
                    <strong>{trackedLabel(c)}</strong>
                    {c.drug_name && <span className="muted">· {c.drug_name}</span>}
                    {c.event_id === primaryId && <span className="tag">Primary</span>}
                    {earlier && <span className="tag tag-warn">Earlier than primary event</span>}
                    {beforeHorizon && <span className="tag tag-warn">Before planned exit</span>}
                    {c.status !== "active" && <span className={`pill pill-${c.status}`}>{STATUS_TEXT[c.status]}</span>}
                    <KindBadge kind="fact" />
                  </div>
                  {c.indications.length > 0 && <p className="tl-meta">{c.indications.join(", ")}</p>}
                  <p className="tl-meta">
                    BPIQ {c.stage_label ?? "—"} / {c.event_label ?? "—"} · <code>{c.event_id}</code>
                  </p>
                  {c.note && <p className="catalyst-note">{c.note}</p>}
                  {flags.length > 0 && (
                    <p className="tl-meta">
                      Provider labels (not holdings or trades):{" "}
                      {flags.map(([k]) => (
                        <span key={k} className="tag">
                          {k.replace(/^is_/, "").replaceAll("_", " ")}
                        </span>
                      ))}
                    </p>
                  )}
                  {c.status === "not_returned" && (
                    <p className="tl-warn">
                      Absent from a complete BPIQ query since {dateTime(c.not_returned_since)}. This may be a cancellation, completion or data
                      change; it is not confirmed. Last known values are kept.
                    </p>
                  )}
                  {eventRevisions.length > 0 && (
                    <ul className="revisions" aria-label="Revision history">
                      {eventRevisions.map((r, i) => (
                        <li key={i}>
                          <span className="rev-kind">{REVISION_TEXT[r.kind] ?? r.kind}</span>
                          {(r.previous || r.current) && (
                            <span className="rev-values">
                              <del>{r.previous ?? "—"}</del> → <ins>{r.current ?? "—"}</ins>
                            </span>
                          )}
                          <span className="muted">observed {dateTime(r.observed_at)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {c.outcome && <OutcomeBox outcome={c.outcome} />}
                  <p className="tl-meta">
                    First seen {dateTime(c.first_seen_at)} · last seen {dateTime(c.last_seen_at)}
                    {c.source_url && (
                      <>
                        {" · "}
                        <a href={c.source_url} target="_blank" rel="noreferrer noopener" className="link">
                          Catalyst source <ExternalLink aria-hidden size={11} />
                        </a>
                      </>
                    )}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {historical.length > 0 && (
        <details className="subdetails">
          <summary>Past events from BPIQ historical catalysts ({historical.length})</summary>
          <ul className="history-list">
            {historical.map((o) => (
              <li key={o.provider_record_id}>
                <OutcomeBox outcome={o} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function OutcomeBox({ outcome }: { outcome: OutcomeRecord }) {
  return (
    <div className="outcome">
      <div className="tl-title">
        <strong>{shortDate(outcome.catalyst_date)}</strong>
        <span className="muted">
          {outcome.stage ?? "—"} · {outcome.drug_name ?? "—"}
        </span>
        <KindBadge kind="fact" />
      </div>
      {outcome.text && <p>{outcome.text}</p>}
      {outcome.detailed_text && <p className="muted">{outcome.detailed_text}</p>}
      {outcome.match_basis && <p className="tl-warn">{outcome.match_basis}</p>}
      <p className="tl-meta">
        {outcome.open_price_gap_percent != null && `Provider open gap field: ${outcome.open_price_gap_percent} (units not documented) · `}
        Updated {dateTime(outcome.provider_updated_at)}
        {outcome.source_url && (
          <>
            {" · "}
            <a href={outcome.source_url} target="_blank" rel="noreferrer noopener" className="link">
              Source <ExternalLink aria-hidden size={11} />
            </a>
          </>
        )}
      </p>
    </div>
  );
}
