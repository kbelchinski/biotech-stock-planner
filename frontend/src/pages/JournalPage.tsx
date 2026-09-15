import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Download, Trash2 } from "lucide-react";
import { api, errorMessage, researchApi } from "../api";
import { dateTime, pct, price, shortDate } from "../format";
import { href } from "../router";
import type { JournalEntry, PerformanceSummary, Trade } from "../researchTypes";
import { Empty, ErrorState, Loading, Notes, Panel, Stat } from "../components/research/ui";
import { netReturn } from "./PlansPage";

interface Props {
  scenario: string | null;
}

type Perf = Awaited<ReturnType<typeof researchApi.performance>>;
type History = Awaited<ReturnType<typeof researchApi.scanHistory>>;

const KIND_TITLES: { key: "actual" | "hypothetical" | "paper"; title: string; subtitle: string }[] = [
  { key: "actual", title: "Actual positions", subtitle: "Manually entered positions only." },
  { key: "hypothetical", title: "Hypothetical trades", subtitle: "Trades you chose not to place." },
  { key: "paper", title: "Forward paper tracking", subtitle: "Automated from qualifying scans. Next open entry; close before catalyst exit; 0.10% cost + 0.25% slippage per side." },
];

export function JournalPage({ scenario }: Props) {
  const [perf, setPerf] = useState<Perf | null>(null);
  const [paper, setPaper] = useState<Trade[] | null>(null);
  const [journal, setJournal] = useState<JournalEntry[] | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, trades, j, h] = await Promise.all([researchApi.performance(scenario), researchApi.trades(), researchApi.journal(), researchApi.scanHistory()]);
      setPerf(p);
      setPaper(trades.filter((t) => t.kind === "paper"));
      setJournal(j);
      setHistory(h);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [scenario]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="page-stack">
      {error && <ErrorState message={error} onRetry={load} />}
      <Panel
        title="Performance"
        id="performance"
        subtitle={`Each group is calculated separately and never combined. Benchmark: ${perf?.benchmark ?? "—"}. Forward tracking is not a historical backtest.`}
      >
        {perf === null && !error && <Loading label="Calculating performance…" />}
        {perf && <Notes items={perf.issues} />}
        {perf && (
          <div className="perf-grid">
            {KIND_TITLES.map((k) => (
              <PerfCard key={k.key} title={k.title} subtitle={k.subtitle} s={perf.summaries[k.key]} />
            ))}
          </div>
        )}
        {perf && (
          <details className="subdetails">
            <summary>Calculation conventions</summary>
            <Notes items={perf.summaries.actual.conventions} tone="info" />
          </details>
        )}
      </Panel>

      <Panel title="Forward paper trades" id="paper" subtitle="Created only after a scan finishes; entries always occur in a later session than the scan's price data.">
        {paper === null ? (
          <Loading label="Loading paper trades…" />
        ) : paper.length === 0 ? (
          <Empty title="No paper trades yet">They are created automatically when a scan finds qualifying companies with a dated catalyst.</Empty>
        ) : (
          <div className="mini-table-wrap">
            <table className="mini-table">
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">Status</th>
                  <th scope="col">Category</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Planned exit</th>
                  <th scope="col">Exit</th>
                  <th scope="col" className="num">Net return</th>
                  <th scope="col">History</th>
                </tr>
              </thead>
              <tbody>
                {paper.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <a className="ticker link-plain" href={href("research", t.ticker)}>
                        {t.ticker}
                      </a>
                    </td>
                    <td>
                      <span className={`pill pill-${t.status}`}>{t.status.replaceAll("_", " ")}</span>
                    </td>
                    <td>{t.catalyst_category ?? "—"}</td>
                    <td>{t.entry_price != null ? `${price(t.entry_price)} open · ${shortDate(t.entry_date)}` : `pending · ${shortDate(t.entry_date)}`}</td>
                    <td>{shortDate(t.planned_exit_date)}</td>
                    <td>{t.exit_price != null ? `${price(t.exit_price)} close · ${shortDate(t.exit_date)}` : "—"}</td>
                    <td className="num">{pct(netReturn(t))}</td>
                    <td>
                      <details>
                        <summary>{t.history.length} event(s)</summary>
                        <Notes items={t.history} tone="info" />
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <JournalSection entries={journal} onChanged={load} />

      <Panel title="Scan history" id="scan-history" subtitle="Immutable snapshots of every scan, including provider records used, rule version, decisions and data-quality issues.">
        {history === null ? (
          <Loading label="Loading scan history…" />
        ) : history.length === 0 ? (
          <Empty title="No scans saved yet" />
        ) : (
          <div className="mini-table-wrap">
            <table className="mini-table">
              <thead>
                <tr>
                  <th scope="col">Finished</th>
                  <th scope="col">Outcome</th>
                  <th scope="col" className="num">Evaluated</th>
                  <th scope="col" className="num">Qualifying</th>
                  <th scope="col" className="num">Issues</th>
                  <th scope="col">Rule version</th>
                  <th scope="col">Export</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td>{dateTime(h.finished_at)}</td>
                    <td>{h.outcome.replaceAll("_", " ")}</td>
                    <td className="num">{h.summary.evaluated}</td>
                    <td className="num">{h.summary.qualifying}</td>
                    <td className="num">{h.issue_count}</td>
                    <td>
                      <code>{h.rule_version}</code>
                    </td>
                    <td>
                      <a className="link" href={api.exportUrl(h.id)} download>
                        <Download aria-hidden size={12} /> CSV
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">
          Storage note: snapshots keep normalized provider records locally for your own review. Check your BPIQ and Alpaca terms before exporting or sharing them.
        </p>
      </Panel>
    </div>
  );
}

function PerfCard({ title, subtitle, s }: { title: string; subtitle: string; s: PerformanceSummary }) {
  return (
    <article className="perf-card">
      <h3>{title}</h3>
      <p className="small muted">{subtitle}</p>
      <dl className="stat-grid stat-grid-compact">
        <Stat label="Completed trades" value={s.closed_count} hint={s.observation_start ? `${shortDate(s.observation_start)} – ${shortDate(s.observation_end)}` : "No observation period yet"} />
        <Stat label="Open" value={s.open_count} hint={s.unresolved_count ? `${s.unresolved_count} pending/unavailable` : undefined} />
        <Stat label="Win rate" value={s.win_rate != null ? `${(s.win_rate * 100).toFixed(0)}%` : "—"} />
        <Stat label="Average win" value={pct(s.average_win)} />
        <Stat label="Average loss" value={pct(s.average_loss)} />
        <Stat label="Max drawdown" value={pct(s.max_drawdown)} />
        <Stat label="Compounded" value={pct(s.total_compounded_return)} />
        <Stat label="Vs benchmark (avg)" value={pct(s.benchmark_relative_average)} hint={`${s.benchmark_coverage} of ${s.closed_count} with benchmark data`} />
      </dl>
      <Notes items={s.warnings} />
      {s.worst.length > 0 && (
        <details className="subdetails">
          <summary>Worst outcomes</summary>
          <ul className="plain-list">
            {s.worst.map((w) => (
              <li key={w.trade_id}>
                {w.ticker} {pct(w.net_return)} ({shortDate(w.entry_date)} – {shortDate(w.exit_date)}, {w.category})
              </li>
            ))}
          </ul>
        </details>
      )}
      {s.by_category.length > 0 && (
        <details className="subdetails">
          <summary>By catalyst category</summary>
          <table className="mini-table">
            <thead>
              <tr>
                <th scope="col">Category</th>
                <th scope="col" className="num">n</th>
                <th scope="col" className="num">Win rate</th>
                <th scope="col" className="num">Avg net</th>
              </tr>
            </thead>
            <tbody>
              {s.by_category.map((c) => (
                <tr key={c.category}>
                  <td>{c.category}</td>
                  <td className="num">{c.count}</td>
                  <td className="num">{c.win_rate != null ? `${(c.win_rate * 100).toFixed(0)}%` : "—"}</td>
                  <td className="num">{pct(c.average_net_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {s.open_positions.length > 0 && (
        <details className="subdetails">
          <summary>Open positions ({s.open_positions.length}) — not in statistics</summary>
          <ul className="plain-list">
            {s.open_positions.map((o) => (
              <li key={o.trade_id}>
                {o.ticker} entered {price(o.entry_price)} on {shortDate(o.entry_date)} · mark {price(o.mark_close)} ({shortDate(o.mark_date)}) · unrealized{" "}
                {pct(o.unrealized_net_return)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

function JournalSection({ entries, onChanged }: { entries: JournalEntry[] | null; onChanged: () => void }) {
  const [f, setF] = useState({ ticker: "", decision: "enter", thesis: "", expected_catalyst: "", reasons: "", result_note: "" });
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [key]: e.target.value });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await researchApi.addJournal({ ...f, ticker: f.ticker.trim().toUpperCase() });
      setF({ ticker: "", decision: "enter", thesis: "", expected_catalyst: "", reasons: "", result_note: "" });
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Delete this journal entry?")) return;
    try {
      await researchApi.deleteJournal(id);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <Panel title="Decision journal" id="journal" subtitle="Record why you entered, skipped or exited — including decisions not to act.">
      <form onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            <span>Ticker</span>
            <input className="input" value={f.ticker} onChange={set("ticker")} />
          </label>
          <label className="field">
            <span>Decision</span>
            <select className="input" value={f.decision} onChange={set("decision")}>
              <option value="enter">Enter</option>
              <option value="skip">Skip</option>
              <option value="exit">Exit</option>
              <option value="note">Note</option>
            </select>
          </label>
          <label className="field">
            <span>Expected catalyst</span>
            <input className="input" value={f.expected_catalyst} onChange={set("expected_catalyst")} />
          </label>
        </div>
        <div className="form-grid form-grid-3">
          <label className="field">
            <span>Thesis</span>
            <textarea className="input" rows={2} value={f.thesis} onChange={set("thesis")} />
          </label>
          <label className="field">
            <span>Reasons</span>
            <textarea className="input" rows={2} value={f.reasons} onChange={set("reasons")} />
          </label>
          <label className="field">
            <span>Actual or hypothetical result</span>
            <textarea className="input" rows={2} value={f.result_note} onChange={set("result_note")} />
          </label>
        </div>
        {error && <p className="notes-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary btn-sm">
            Add entry
          </button>
        </div>
      </form>
      {entries === null ? (
        <Loading label="Loading journal…" />
      ) : entries.length === 0 ? (
        <Empty title="No journal entries yet" />
      ) : (
        <ul className="journal-list">
          {entries.map((e) => (
            <li key={e.id}>
              <div className="journal-head">
                <span className={`pill pill-${e.decision}`}>{e.decision}</span>
                <a className="ticker link-plain" href={href("research", e.ticker)}>
                  {e.ticker}
                </a>
                <span className="muted small">{dateTime(e.created_at)}</span>
                <button type="button" className="btn btn-ghost btn-sm btn-icon" onClick={() => remove(e.id)} aria-label="Delete entry">
                  <Trash2 aria-hidden size={14} />
                </button>
              </div>
              <dl className="kv kv-compact">
                {e.expected_catalyst && (
                  <div>
                    <dt>Expected catalyst</dt>
                    <dd>{e.expected_catalyst}</dd>
                  </div>
                )}
                {e.thesis && (
                  <div>
                    <dt>Thesis</dt>
                    <dd>{e.thesis}</dd>
                  </div>
                )}
                {e.reasons && (
                  <div>
                    <dt>Reasons</dt>
                    <dd>{e.reasons}</dd>
                  </div>
                )}
                {e.result_note && (
                  <div>
                    <dt>Result</dt>
                    <dd>{e.result_note}</dd>
                  </div>
                )}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
