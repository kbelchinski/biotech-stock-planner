import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Bookmark, Calculator, Eye, EyeOff, LoaderCircle, MessageCircleQuestion, Search, Sparkles } from "lucide-react";
import { errorMessage, researchApi } from "../api";
import { dateTime, metricValue, pct, price, shortDate, usd } from "../format";
import { href } from "../router";
import type { AskResponse, CritiqueResponse, EvidenceItem, McpSection, Research } from "../researchTypes";
import { CriterionCard } from "../components/CompanyDrawer";
import { PriceChart } from "../components/PriceChart";
import { EligibilityBadge } from "../components/StatusBadge";
import { CatalystTimeline } from "../components/research/CatalystTimeline";
import { Empty, ErrorState, KindBadge, KindLegend, Loading, Notes, Panel, SourceLine, type Kind } from "../components/research/ui";

interface Props {
  ticker: string | null;
  mode: string;
  scenario: string | null;
  onWatchChanged: () => void;
}

const EVIDENCE_GROUPS: { key: EvidenceItem["category"]; title: string }[] = [
  { key: "supporting", title: "Evidence supporting the setup" },
  { key: "against", title: "Evidence against" },
  { key: "missing_or_stale", title: "Missing or stale" },
  { key: "changes", title: "Changes" },
  { key: "invalidation_conditions", title: "Would invalidate the thesis" },
  { key: "context", title: "Context (no direction implied)" },
];

export function ResearchPage({ ticker, mode, scenario, onWatchChanged }: Props) {
  const [query, setQuery] = useState(ticker ?? "");
  const [data, setData] = useState<Research | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!ticker) return;
    setLoading(true);
    setError(null);
    try {
      setData(await researchApi.research(ticker, scenario));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [ticker, scenario]);

  useEffect(() => {
    setQuery(ticker ?? "");
    setData(null);
    void load();
  }, [ticker, load]);

  const go = (event: FormEvent) => {
    event.preventDefault();
    const clean = query.trim().toUpperCase();
    if (/^[A-Z.]{1,10}$/.test(clean)) window.location.hash = href("research", clean);
  };

  const search = (
    <form className="inline-form" onSubmit={go} role="search">
      <label className="field">
        <span className="sr-only">Ticker</span>
        <input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ticker" autoComplete="off" />
      </label>
      <button type="submit" className="btn btn-secondary">
        <Search aria-hidden size={15} /> Open
      </button>
    </form>
  );

  if (!ticker) {
    return (
      <div className="page-stack">
        <Panel title="Company research" subtitle="Evidence, uncertainty and changes for one company. Nothing here is a recommendation." actions={search}>
          <Empty title="Choose a company">Enter a ticker, or open one from the Screener or Watchlist.</Empty>
        </Panel>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <Panel title={`Research · ${ticker}`} subtitle="Facts, calculations, your assumptions and AI interpretations are labelled separately." actions={search}>
        <KindLegend />
      </Panel>
      {loading && !data && <Loading label={`Loading research for ${ticker} (provider requests may take a moment)…`} />}
      {error && <ErrorState message={error} onRetry={load} />}
      {data && <ResearchBody data={data} mode={mode} scenario={scenario} reload={load} loading={loading} onWatchChanged={onWatchChanged} />}
    </div>
  );
}

function ResearchBody({
  data,
  mode,
  scenario,
  reload,
  loading,
  onWatchChanged,
}: {
  data: Research;
  mode: string;
  scenario: string | null;
  reload: () => Promise<void>;
  loading: boolean;
  onWatchChanged: () => void;
}) {
  const [note, setNote] = useState("");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const result = data.screening.result;
  const issues = [...data.issues, ...data.catalysts.issues, ...(data.price_context_error ? [`Price context: ${data.price_context_error}`] : [])];

  const toggleWatch = async () => {
    setBusy(true);
    try {
      if (data.overview.watched) await researchApi.unwatch(data.ticker);
      else await researchApi.watch(data.ticker, undefined, data.overview.name);
      onWatchChanged();
      await reload();
    } catch (e) {
      setSaveMessage(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setSaveMessage(null);
    try {
      const saved = await researchApi.saveAnalysis(data.ticker, note, scenario);
      setSaveMessage(`Analysis saved ${dateTime(saved.created_at)}. Future visits show changes since this point.`);
      setNote("");
    } catch (e) {
      setSaveMessage(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="panel research-head" aria-label="Company overview">
        <div className="research-id">
          <div className="drawer-title-row">
            <h2>{data.ticker}</h2>
            {data.overview.exchange && <span className="tag">{data.overview.exchange}</span>}
            {mode === "demo" && <span className="tag tag-mock">Demo data</span>}
            {result && <EligibilityBadge value={result.eligibility} />}
          </div>
          <p className="drawer-subtitle">{data.overview.name ?? "Company name unavailable"}</p>
          <dl className="stat-row">
            <div>
              <dt>Market cap</dt>
              <dd>
                {usd(data.overview.market_cap_usd)} <KindBadge kind="fact" />
              </dd>
            </div>
            <div>
              <dt>BPIQ last price (display only)</dt>
              <dd>{data.overview.provider_last_price ? `$${data.overview.provider_last_price}` : "—"}</dd>
            </div>
            <div>
              <dt>Latest completed session</dt>
              <dd>{shortDate(data.latest_completed_session)}</dd>
            </div>
            <div>
              <dt>Generated</dt>
              <dd>{dateTime(data.generated_at)}</dd>
            </div>
          </dl>
          {data.overview.market_cap_source && <SourceLine source={data.overview.market_cap_source} />}
        </div>
        <div className="research-actions">
          <button type="button" className="btn btn-secondary" onClick={toggleWatch} disabled={busy}>
            {data.overview.watched ? <EyeOff aria-hidden size={15} /> : <Eye aria-hidden size={15} />}
            {data.overview.watched ? "Stop watching" : "Watch"}
          </button>
          <a className="btn btn-secondary" href={href("plans", data.ticker)}>
            <Calculator aria-hidden size={15} /> Plan a trade
          </a>
          <button type="button" className="btn btn-ghost" onClick={() => void reload()} disabled={loading}>
            {loading ? <LoaderCircle aria-hidden size={15} className="spin" /> : null} Reload
          </button>
          <div className="save-analysis">
            <label className="field">
              <span>Note for this saved analysis</span>
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={4000} />
            </label>
            <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy}>
              <Bookmark aria-hidden size={14} /> Save analysis
            </button>
          </div>
          {saveMessage && <p className="small" role="status">{saveMessage}</p>}
        </div>
      </section>

      {issues.length > 0 && (
        <div className="alert alert-warn" role="note">
          <div className="alert-body">
            <strong>Partial data</strong>
            <Notes items={issues} />
          </div>
        </div>
      )}

      <Panel title="Key questions" id="questions" subtitle="Short answers from connected data. Unknown means not available, not zero risk.">
        <div className="question-grid">
          {data.questions.map((q) => (
            <article key={q.id} className={`question${q.known ? "" : " is-unknown"}`}>
              <h3>{q.question}</h3>
              <p>{q.answer}</p>
              <KindBadge kind={(q.kind === "calculation" ? "calculation" : "fact") as Kind} />
            </article>
          ))}
        </div>
      </Panel>

      <Changes data={data} />

      <Panel
        title="Screening results"
        id="screening"
        subtitle={data.screening.note}
      >
        {result && data.screening.scan ? (
          <>
            <p className="small muted">
              Scan <code>{data.screening.scan.id.slice(0, 8)}</code> · {shortDate(data.screening.scan.scan_date)} · outcome {data.screening.scan.outcome.replaceAll("_", " ")} · rule
              version <code>{data.screening.scan.rule_version}</code>
            </p>
            <div className="criteria-list">
              {result.criteria
                .filter((c) => c.key !== "runway" || c.status !== "not_applied")
                .map((c) => (
                  <CriterionCard key={c.key} criterion={c} />
                ))}
            </div>
          </>
        ) : (
          <Empty title="Not evaluated">Run a scan that includes this company to see observed values against thresholds.</Empty>
        )}
        {data.scan_history.length > 1 && (
          <details className="subdetails">
            <summary>Eligibility in saved scans ({data.scan_history.length})</summary>
            <table className="mini-table">
              <thead>
                <tr>
                  <th scope="col">Scan date</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="num">Close used</th>
                </tr>
              </thead>
              <tbody>
                {data.scan_history.map((h) => (
                  <tr key={h.scan_id}>
                    <td>{shortDate(h.scan_date)}</td>
                    <td>{h.eligibility.replaceAll("_", " ")}</td>
                    <td className="num">{price(h.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </Panel>

      <Panel
        title="Catalyst timeline"
        id="catalysts"
        subtitle={
          data.catalysts.origin === "tracked"
            ? `Tracked from watchlist refreshes · last successful refresh ${dateTime(data.catalysts.retrieved_at)}`
            : `One-off BPIQ query · retrieved ${dateTime(data.catalysts.retrieved_at)}`
        }
      >
        <CatalystTimeline
          items={data.catalysts.items}
          revisions={data.catalysts.revisions}
          today={data.today}
          primaryId={data.catalysts.primary_event_id}
          earlierIds={data.catalysts.earlier_than_primary}
          historical={data.catalysts.historical}
        />
      </Panel>

      <PriceSection data={data} />
      <FinancialSection data={data} />
      <OwnershipSection data={data} />
      <EvidenceSection evidence={data.evidence} />
      <CritiqueSection ticker={data.ticker} scenario={scenario} />
    </>
  );
}

function Changes({ data }: { data: Research }) {
  if (!data.changes) {
    return (
      <p className="small muted panel-note">No saved analysis yet. Save one to see what changes before your next review.</p>
    );
  }
  return (
    <Panel title="Changes since previous saved analysis" id="changes" subtitle={`Compared with the analysis saved ${dateTime(data.changes.previous_saved_at)}.`}>
      {data.changes.items.length === 0 ? (
        <p className="muted">No tracked values changed.</p>
      ) : (
        <div className="mini-table-wrap">
          <table className="mini-table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Previous</th>
                <th scope="col">Current</th>
              </tr>
            </thead>
            <tbody>
              {data.changes.items.map((c) => (
                <tr key={c.key}>
                  <td>
                    <code>{c.key}</code>
                  </td>
                  <td>
                    <del>{c.previous ?? "—"}</del>
                  </td>
                  <td>
                    <ins>{c.current ?? "—"}</ins>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function PriceSection({ data }: { data: Research }) {
  const ctx = data.price_context;
  if (!ctx) {
    return (
      <Panel title="Price and volume context" id="price">
        <ErrorState message={data.price_context_error ?? "Price context unavailable."} />
      </Panel>
    );
  }
  return (
    <Panel
      title="Price and volume context"
      id="price"
      subtitle={`Context only — not a filter or signal. ${ctx.present_sessions} of ${ctx.expected_sessions} sessions have bars since ${shortDate(ctx.window_start)}. ${ctx.freshness}`}
    >
      <PriceChart
        ticker={data.ticker}
        bars={ctx.chart_bars}
        note={`Alpaca daily bars, feed=${ctx.feed}, adjustment=${ctx.adjustment} (split-adjusted). Retrieved ${dateTime(ctx.retrieved_at)}.`}
      />
      <div className="mini-table-wrap">
        <table className="mini-table metrics-table">
          <caption className="sr-only">Price and volume metrics</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col" className="num">Value</th>
              <th scope="col">Lookback</th>
              <th scope="col">How it is calculated</th>
            </tr>
          </thead>
          <tbody>
            {ctx.metrics.map((m) => (
              <tr key={m.key} className={m.status === "ok" ? "" : "is-unavailable"}>
                <td>
                  {m.label} <KindBadge kind="calculation" />
                </td>
                <td className="num">{m.status === "ok" ? metricValue(m) : <span className="pill pill-unknown">{m.status.replaceAll("_", " ")}</span>}</td>
                <td>{m.lookback}</td>
                <td>
                  <details>
                    <summary>{m.formula}</summary>
                    {m.detail && <p className="small">{m.detail}</p>}
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="section-title">Large overnight gaps (|gap| ≥ {ctx.gap_threshold_pct}%)</h3>
      {ctx.gaps.length === 0 ? (
        <p className="muted small">None in the last 60 sessions with bars on both sides.</p>
      ) : (
        <ul className="plain-list">
          {ctx.gaps.map((g) => (
            <li key={g.session}>
              {shortDate(g.session)}: open {price(g.open)} vs prior close {price(g.previous_close)} ({shortDate(g.previous_session)}) ·{" "}
              <strong>{pct(g.gap_pct / 100)}</strong>
            </li>
          ))}
        </ul>
      )}
      <details className="subdetails">
        <summary>Conventions and data coverage</summary>
        <Notes items={ctx.conventions} tone="info" />
      </details>
    </Panel>
  );
}

function McpUnavailable({ section }: { section: McpSection }) {
  return (
    <p className="callout callout-warn">
      <strong>Unknown / unavailable from connected sources.</strong> {section.reason} Missing data is not evidence of low risk.
    </p>
  );
}

function FinancialSection({ data }: { data: Research }) {
  const fin = data.financials.mcp;
  const runway = data.financials.screening_runway;
  return (
    <Panel
      title="Financial risk"
      id="financials"
      subtitle="Passing a runway threshold does not make a company safe. Shelf or ATM capacity is not an announced issuance."
    >
      {runway && runway.status !== "not_applied" && (
        <div className={`callout ${runway.is_mock ? "callout-mock" : "callout-warn"}`}>
          Screening runway input: {runway.observed ?? "unknown"} — {runway.explanation}
        </div>
      )}
      {fin.state === "ok" ? (
        <div className="mini-table-wrap">
          <table className="mini-table">
            <thead>
              <tr>
                <th scope="col">Measure</th>
                <th scope="col" className="num">Value</th>
                <th scope="col">Units / period</th>
                <th scope="col">Reference date</th>
                <th scope="col">Type</th>
                <th scope="col">Notes</th>
              </tr>
            </thead>
            <tbody>
              {fin.records.map((raw, i) => {
                const r = raw as Record<string, string | number | boolean | null>;
                return (
                  <tr key={i}>
                    <td>{String(r.label)}</td>
                    <td className="num">{r.value == null ? "Unknown" : typeof r.value === "number" ? r.value.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(r.value)}</td>
                    <td>{[r.units, r.reference_period].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{shortDate(r.reference_date as string | null)}</td>
                    <td>
                      <KindBadge kind={r.provenance === "calculated" ? "calculation" : "fact"} />
                      {!r.mapping_verified && <span className="tag tag-warn">Unverified mapping</span>}
                    </td>
                    <td className="wrap">
                      {r.raw_field && <code>{String(r.raw_field)}</code>} {r.formula && <span className="small">{String(r.formula)}. </span>}
                      {r.note && <span className="small">{String(r.note)}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="small muted">Retrieved via BPIQ MCP tool {fin.tool}.</p>
        </div>
      ) : (
        <McpUnavailable section={fin} />
      )}
    </Panel>
  );
}

function OwnershipSection({ data }: { data: Research }) {
  const insiders = data.insiders.mcp;
  const funds = data.funds.mcp;
  const flagged = data.funds.provider_flags.filter((f) => f.is_hedge_fund_pick === true || f.is_hedge_fund_avoid === true);
  return (
    <Panel
      title="Insider and hedge fund activity"
      id="ownership"
      subtitle="Supporting research signals from filings. They never override screening rules and are not live trades."
    >
      <h3 className="section-title">Insider transactions</h3>
      {insiders.state === "ok" ? (
        <div className="mini-table-wrap">
          <p className="small muted">
            {insiders.records.length} transaction{insiders.records.length === 1 ? "" : "s"}. Scroll the table for more.
          </p>
          <div className="mini-table-scroll" tabIndex={0} aria-label="Insider transactions">
          <table className="mini-table">
            <thead>
              <tr>
                <th scope="col">Insider</th>
                <th scope="col">Type</th>
                <th scope="col">Transaction</th>
                <th scope="col">Filed</th>
                <th scope="col" className="num">Shares</th>
                <th scope="col" className="num">Price</th>
                <th scope="col" className="num">Value</th>
                <th scope="col" className="num">Owned after</th>
                <th scope="col">Filing</th>
              </tr>
            </thead>
            <tbody>
              {insiders.records.map((raw, i) => {
                const r = raw as Record<string, string | number | null>;
                return (
                  <tr key={i}>
                    <td>
                      {r.insider_name ?? "—"}
                      {r.role && <span className="sub"> · {r.role}</span>}
                    </td>
                    <td>
                      {String(r.transaction_type).replaceAll("_", " ")}
                      {r.transaction_code && <code> {r.transaction_code}</code>}
                      {r.security_type && <span className="sub"> · {r.security_type}</span>}
                      {r.note && <span className="sub"> · {r.note}</span>}
                    </td>
                    <td>{shortDate(r.transaction_date as string | null)}</td>
                    <td>{shortDate(r.filing_date as string | null)}</td>
                    <td className="num">{r.shares?.toLocaleString() ?? "—"}</td>
                    <td className="num">{price(r.price as number | null)}</td>
                    <td className="num">{usd(r.value_usd as number | null)}</td>
                    <td className="num">{r.shares_owned_after?.toLocaleString() ?? "—"}</td>
                    <td>
                      {r.source_url ? (
                        <a className="link" href={String(r.source_url)} target="_blank" rel="noreferrer noopener">
                          Filing
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <p className="small muted">
            Field mapping unverified. BPIQ reports only an acquired (A) / disposed (D) flag, so purchases, awards, exercises and sales cannot be
            distinguished; they are shown as "acquisition/disposal unspecified". No filing links or post-transaction holdings are provided.
          </p>
        </div>
      ) : (
        <McpUnavailable section={insiders} />
      )}

      <h3 className="section-title">Hedge fund holdings (13F filings, reported with a delay)</h3>
      {funds.state === "ok" ? (
        <div className="mini-table-wrap">
          <p className="small muted">
            {funds.records.length} holding{funds.records.length === 1 ? "" : "s"}. Scroll the table for more.
          </p>
          <div className="mini-table-scroll" tabIndex={0} aria-label="Hedge fund holdings">
          <table className="mini-table">
            <thead>
              <tr>
                <th scope="col">Fund</th>
                <th scope="col">Holdings period</th>
                <th scope="col">Filed</th>
                <th scope="col" className="num">Shares</th>
                <th scope="col" className="num">Value</th>
                <th scope="col" className="num">Change</th>
                <th scope="col">Change basis</th>
              </tr>
            </thead>
            <tbody>
              {funds.records.map((raw, i) => {
                const r = raw as Record<string, string | number | null>;
                return (
                  <tr key={i}>
                    <td>{r.fund ?? "—"}</td>
                    <td>{shortDate(r.period_end as string | null)}</td>
                    <td>{r.filing_date ? shortDate(r.filing_date as string) : "Not provided"}</td>
                    <td className="num">{r.shares?.toLocaleString() ?? "—"}</td>
                    <td className="num">{usd(r.value_usd as number | null)}</td>
                    <td className="num">{r.change_shares?.toLocaleString() ?? "—"}</td>
                    <td className="wrap small">{r.change_basis ?? "Not available"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </div>
      ) : (
        <McpUnavailable section={funds} />
      )}

      <h3 className="section-title">BPIQ provider labels (separate from holdings)</h3>
      {flagged.length === 0 ? (
        <p className="muted small">No hedge-fund pick/avoid labels on this company's catalyst records.</p>
      ) : (
        <ul className="plain-list">
          {flagged.map((f) => (
            <li key={f.event_id}>
              <code>{f.event_id}</code>: {f.is_hedge_fund_pick === true && <span className="tag">Hedge fund pick</span>}{" "}
              {f.is_hedge_fund_avoid === true && <span className="tag tag-warn">Hedge fund avoid</span>} <KindBadge kind="fact" />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function EvidenceSection({ evidence }: { evidence: EvidenceItem[] }) {
  return (
    <Panel title="Evidence" id="evidence" subtitle="Built deterministically from the records above. Useful without any AI provider.">
      <div className="evidence-grid">
        {EVIDENCE_GROUPS.map((group) => {
          const items = evidence.filter((e) => e.category === group.key);
          return (
            <section key={group.key} className={`evidence-col ev-${group.key}`} aria-label={group.title}>
              <h3>
                {group.title} <span className="count">{items.length}</span>
              </h3>
              {items.length === 0 ? (
                <p className="muted small">None found in connected data.</p>
              ) : (
                <ul>
                  {items.map((e) => (
                    <li key={e.id}>
                      <span className="ev-id">{e.id}</span> <KindBadge kind={e.kind} />
                      <p>{e.text}</p>
                      {e.source && <SourceLine source={e.source} />}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </Panel>
  );
}

const ANSWER_TOKEN = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\[((?:E\d+|D:[a-z_]+)(?:\s*,\s*(?:E\d+|D:[a-z_]+))*)\]/g;
const ANSWER_HEADINGS = new Set(["short answer", "supporting evidence", "risks and evidence against", "unknowns and what to check"]);

function AnswerInline({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(ANSWER_TOKEN)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    if (match[2]) {
      parts.push(
        <a key={index} className="link" href={match[2]} target="_blank" rel="noreferrer noopener">
          {match[1]}
        </a>,
      );
    } else {
      match[3].split(",").forEach((id) =>
        parts.push(
          <span key={`${index}-${id}`} className="ev-id">
            {id.trim()}
          </span>,
        ),
      );
    }
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

/** Renders model text safely (no HTML): headings, "- " bullets, links and evidence tags. */
function AnswerText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      const items = bullets;
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="plain-list">
          {items.map((b, i) => (
            <li key={i}>
              <AnswerInline text={b} />
            </li>
          ))}
        </ul>,
      );
      bullets = [];
    }
  };
  text.split("\n").forEach((raw) => {
    const line = raw.trim();
    if (!line) return flush();
    const heading = line.replace(/^#+\s*/, "").replace(/\*\*/g, "").replace(/:$/, "");
    if (ANSWER_HEADINGS.has(heading.toLowerCase()) || /^#+\s/.test(line)) {
      flush();
      blocks.push(
        <h3 key={`h-${blocks.length}`} className="section-title">
          {heading}
        </h3>,
      );
    } else if (/^[-•*]\s+/.test(line)) {
      bullets.push(line.replace(/^[-•*]\s+/, "").replace(/\*\*/g, ""));
    } else {
      flush();
      blocks.push(
        <p key={`p-${blocks.length}`}>
          <AnswerInline text={line.replace(/\*\*/g, "")} />
        </p>,
      );
    }
  });
  flush();
  return <div className="critique">{blocks}</div>;
}

function AskBox({ ticker, scenario }: { ticker: string; scenario: string | null }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const ask = async (event?: FormEvent) => {
    event?.preventDefault();
    if (question.trim().length < 3) return;
    setRunning(true);
    setError(null);
    try {
      setResult(await researchApi.ask(ticker, question.trim(), scenario));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <section aria-label="Ask a question">
      <form className="inline-form" onSubmit={ask}>
        <label className="field field-grow">
          <span>Ask about {ticker} (uses all research data on this page, plus web search when enabled)</span>
          <textarea
            className="input"
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={1000}
            placeholder="e.g. Is it worth investing now? What could go wrong before the catalyst?"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void ask();
            }}
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={running || question.trim().length < 3}>
          {running ? <LoaderCircle aria-hidden size={15} className="spin" /> : <MessageCircleQuestion aria-hidden size={15} />} Ask
        </button>
      </form>
      {running && <p className="small muted" role="status">Researching… web searches can take up to a minute.</p>}
      {error && <ErrorState message={error} />}
      {result && !result.available && <p className="callout callout-warn">AI unavailable: {result.reason}</p>}
      {result?.available && result.answer && (
        <div className="ask-answer">
          <p className="small">
            <KindBadge kind="ai" /> {result.label} Model {result.model} · {dateTime(result.generated_at)} · est. ${result.estimated_cost_usd?.toFixed(4)}
            {result.web_search_enabled ? ` · ${result.web_searches ?? 0} web search(es)` : " · web search off"}
            {(result.removed_sentences ?? 0) > 0 && ` · removed ${result.removed_sentences} sentence(s) with probabilities, price targets or guarantees`}
          </p>
          <p className="small muted">Question: {result.question}</p>
          <AnswerText text={result.answer} />
          {result.sources && result.sources.length > 0 && (
            <details className="subdetails" open>
              <summary>Web sources ({result.sources.length})</summary>
              <ul className="plain-list">
                {result.sources.map((s) => (
                  <li key={s.url}>
                    <a className="link" href={s.url} target="_blank" rel="noreferrer noopener">
                      {s.title}
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {result.search_queries && result.search_queries.length > 0 && (
            <details className="subdetails">
              <summary>Search queries used</summary>
              <Notes items={result.search_queries} tone="info" />
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function CritiqueSection({ ticker, scenario }: { ticker: string; scenario: string | null }) {
  const [invalidation, setInvalidation] = useState("");
  const [result, setResult] = useState<CritiqueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(await researchApi.critique(ticker, invalidation, scenario));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <Panel
      title="AI assistant (optional)"
      id="critique"
      subtitle="Manual trigger only. Ask answers your question from this page's data and web research, with citations. Summarize info organizes the evidence above. Invented probabilities, price targets and guarantees are removed. Verify before acting."
    >
      <AskBox ticker={ticker} scenario={scenario} />
      <h3 className="section-title">Summarize info</h3>
      <div className="inline-form">
        <label className="field field-grow">
          <span>Your invalidation condition (optional, sent as your assumption)</span>
          <input className="input" value={invalidation} onChange={(e) => setInvalidation(e.target.value)} maxLength={2000} />
        </label>
        <button type="button" className="btn btn-secondary" onClick={run} disabled={running}>
          {running ? <LoaderCircle aria-hidden size={15} className="spin" /> : <Sparkles aria-hidden size={15} />} Summarize info
        </button>
      </div>
      {error && <ErrorState message={error} />}
      {result && !result.available && <p className="callout callout-warn">AI unavailable: {result.reason} The evidence above remains complete without it.</p>}
      {result?.available && result.sections && (
        <div className="critique">
          <p className="small">
            <KindBadge kind="ai" /> {result.label} Model {result.model} · {dateTime(result.generated_at)} · est. ${result.estimated_cost_usd?.toFixed(4)}
            {result.removed && (result.removed.uncited > 0 || result.removed.forbidden > 0) && (
              <> · removed {result.removed.uncited} uncited and {result.removed.forbidden} disallowed point(s)</>
            )}
          </p>
          {Object.entries(result.sections).map(([key, points]) => (
            <section key={key}>
              <h3 className="section-title">{key.replaceAll("_", " ")}</h3>
              {points.length === 0 ? (
                <p className="muted small">Nothing.</p>
              ) : (
                <ul className="plain-list">
                  {points.map((p, i) => (
                    <li key={i}>
                      {p.point}{" "}
                      {p.evidence_ids.map((id) => (
                        <span key={id} className="ev-id">
                          {id}
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </div>
      )}
    </Panel>
  );
}
