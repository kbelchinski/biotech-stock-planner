import { useCallback, useEffect, useState, type FormEvent } from "react";
import { LoaderCircle, Plus, RefreshCw, Trash2 } from "lucide-react";
import { errorMessage, researchApi } from "../api";
import { daysBetween, dateTime, shortDate, trackedLabel } from "../format";
import { href } from "../router";
import type { MonitoringSettings, MonitoringStatus, RefreshJob, WatchItemView } from "../researchTypes";
import type { StatusResponse } from "../types";
import { CatalystTimeline } from "../components/research/CatalystTimeline";
import { Empty, ErrorState, Loading, Notes, Panel } from "../components/research/ui";

interface Props {
  status: StatusResponse;
  scenario: string;
  onScenarioChange: (value: string) => void;
  onChanged: () => void;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function WatchlistPage({ status, scenario, onScenarioChange, onChanged }: Props) {
  const [items, setItems] = useState<WatchItemView[] | null>(null);
  const [monitoring, setMonitoring] = useState<{ settings: MonitoringSettings; status: MonitoringStatus } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ticker, setTicker] = useState("");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [job, setJob] = useState<RefreshJob | null>(null);
  const [lastReport, setLastReport] = useState<RefreshJob["report"]>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, mon] = await Promise.all([researchApi.watchlist(), researchApi.monitoring()]);
      setItems(list.items);
      setMonitoring(mon);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const clean = ticker.trim().toUpperCase();
    if (!/^[A-Z.]{1,10}$/.test(clean)) {
      setFormError("Enter a ticker of 1–10 letters.");
      return;
    }
    setFormError(null);
    try {
      await researchApi.watch(clean, note.trim() || undefined);
      setTicker("");
      setNote("");
      await load();
    } catch (e) {
      setFormError(errorMessage(e));
    }
  };

  const remove = async (value: string) => {
    if (!window.confirm(`Stop watching ${value}? Stored catalyst history is kept.`)) return;
    try {
      await researchApi.unwatch(value);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const refresh = async () => {
    setError(null);
    try {
      let current = await researchApi.startRefresh(status.mode === "demo" ? scenario : null);
      setJob(current);
      while (current.status === "running") {
        await wait(700);
        current = await researchApi.refreshJob(current.id);
        setJob(current);
      }
      if (current.status === "failed") setError(current.error ?? "Refresh failed.");
      setLastReport(current.report);
      await load();
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setJob(null);
    }
  };

  const running = job !== null;
  const failed = lastReport ? Object.entries(lastReport.tickers).filter(([, r]) => !r.ok) : [];

  return (
    <div className="page-stack">
      <Panel
        title="Watchlist"
        id="watchlist"
        subtitle="Watched companies stay tracked regardless of screening eligibility or the discovery window. Watching never means you own a position."
        actions={
          <>
            {status.mode === "demo" && (
              <label className="select-field select-inline">
                <span>Demo scenario</span>
                <select value={scenario} onChange={(e) => onScenarioChange(e.target.value)} disabled={running}>
                  {status.demo_scenarios.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button type="button" className="btn btn-primary" onClick={refresh} disabled={running || !items?.length}>
              {running ? <LoaderCircle aria-hidden size={16} className="spin" /> : <RefreshCw aria-hidden size={16} />}
              {running ? "Refreshing…" : "Refresh now"}
            </button>
          </>
        }
      >
        <form className="inline-form" onSubmit={add}>
          <label className="field">
            <span>Ticker</span>
            <input className="input" value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="e.g. AURX" autoComplete="off" />
          </label>
          <label className="field field-grow">
            <span>Note (optional)</span>
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
          </label>
          <button type="submit" className="btn btn-secondary">
            <Plus aria-hidden size={16} /> Watch
          </button>
        </form>
        {formError && <p className="notes-error" role="alert">{formError}</p>}
        {job && (
          <p className="progress-label" role="status" aria-live="polite">
            {job.message}
          </p>
        )}
        {lastReport && (
          <div className={`alert ${failed.length || lastReport.issues.length ? "alert-warn" : "alert-success"}`} role="status">
            <div className="alert-body">
              <strong>
                Refresh finished: {Object.keys(lastReport.tickers).length - failed.length} of {Object.keys(lastReport.tickers).length} companies updated,{" "}
                {lastReport.notifications_created} new notification(s).
              </strong>
              <Notes items={[...lastReport.issues, ...failed.map(([t, r]) => `${t}: ${r.error} Previous data kept.`)]} />
            </div>
          </div>
        )}
        {error && <ErrorState message={error} onRetry={load} />}
      </Panel>

      {monitoring && <MonitoringCard value={monitoring} onSaved={setMonitoring} />}

      {items === null && !error && <Loading label="Loading watchlist…" />}
      {items && items.length === 0 && (
        <Empty title="No watched companies">Add a ticker above, or use Watch in a company's details on the Screener.</Empty>
      )}
      {items && items.length > 0 && (
        <ul className="watch-list">
          {items.map((item) => (
            <WatchRow key={item.ticker} item={item} today={status.scan_date} onRemove={() => remove(item.ticker)} />
          ))}
        </ul>
      )}
    </div>
  );
}

function WatchRow({ item, today, onRemove }: { item: WatchItemView; today: string; onRemove: () => void }) {
  const next = item.next_catalyst;
  const days = next ? daysBetween(today, next.catalyst_date) : null;
  return (
    <li className="panel watch-row">
      <div className="watch-main">
        <div className="cell-company">
          <a className="ticker link-plain" href={href("research", item.ticker)}>
            {item.ticker}
          </a>
          <span className="company-name">{item.name ?? "Name not loaded yet"}</span>
        </div>
        <div className="watch-next">
          <span className="meta-label">Next dated catalyst</span>
          {next ? (
            <span>
              {trackedLabel(next)} · {shortDate(next.catalyst_date)} {days != null && <span className="muted">({days} days)</span>}
            </span>
          ) : (
            <span className="muted">{item.catalyst_count ? "None upcoming" : "Not refreshed or none returned"}</span>
          )}
        </div>
        <div className="watch-badges">
          <span className="tag">{item.catalyst_count} tracked</span>
          {item.date_revision_count > 0 && <span className="tag tag-warn">{item.date_revision_count} date revision(s)</span>}
          {item.not_returned_count > 0 && <span className="tag tag-warn">{item.not_returned_count} no longer returned</span>}
          {item.undated_count > 0 && <span className="tag">{item.undated_count} undated</span>}
        </div>
        <div className="watch-refresh">
          <span className="meta-label">Last successful refresh</span>
          <span>{item.last_refresh_ok_at ? dateTime(item.last_refresh_ok_at) : "Never"}</span>
          {item.last_refresh_error && <span className="notes-error">Last attempt failed: {item.last_refresh_error}</span>}
        </div>
        <div className="watch-actions">
          <a className="btn btn-secondary btn-sm" href={href("research", item.ticker)}>
            Research
          </a>
          <button type="button" className="btn btn-ghost btn-sm btn-icon" onClick={onRemove} aria-label={`Stop watching ${item.ticker}`}>
            <Trash2 aria-hidden size={15} />
          </button>
        </div>
      </div>
      {item.note && <p className="watch-note">Note: {item.note}</p>}
      {item.catalysts.length > 0 && (
        <details className="subdetails">
          <summary>Full catalyst timeline ({item.catalysts.length})</summary>
          <CatalystTimeline items={item.catalysts} revisions={[]} today={today} />
          <p className="muted small">Revision details are on the research page.</p>
        </details>
      )}
    </li>
  );
}

function MonitoringCard({
  value,
  onSaved,
}: {
  value: { settings: MonitoringSettings; status: MonitoringStatus };
  onSaved: (v: { settings: MonitoringSettings; status: MonitoringStatus }) => void;
}) {
  const [draft, setDraft] = useState(value.settings);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const s = value.status;

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      onSaved(await researchApi.saveMonitoring(draft));
      setMessage("Saved.");
    } catch (e) {
      setMessage(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const setRule = (index: number, patch: Partial<MonitoringSettings["reminder_rules"][number]>) =>
    setDraft({ ...draft, reminder_rules: draft.reminder_rules.map((r, i) => (i === index ? { ...r, ...patch } : r)) });

  return (
    <details className="panel subpanel">
      <summary>
        Monitoring & reminders · daily at {s.daily_refresh_time_ny} New York{" "}
        {s.last_run ? `· last scheduled run ${dateTime(s.last_run.ran_at)}${s.last_run.ok ? "" : " (with errors)"}` : "· no scheduled run yet"}
      </summary>
      <div className="subpanel-body">
        <p className="callout callout-warn">{s.behaviour}</p>
        {!s.process_scheduler_enabled && <p className="notes-error">The scheduler is disabled for this backend process (MONITOR_ENABLED=false).</p>}
        {s.last_error && <p className="notes-error">{s.last_error}</p>}
        <div className="form-grid">
          <label className="check">
            <input type="checkbox" checked={draft.daily_refresh_enabled} onChange={(e) => setDraft({ ...draft, daily_refresh_enabled: e.target.checked })} />
            <span>Daily refresh while the backend runs</span>
          </label>
          <label className="field">
            <span>Time (New York)</span>
            <input className="input" type="time" value={draft.daily_refresh_time_ny} onChange={(e) => setDraft({ ...draft, daily_refresh_time_ny: e.target.value })} />
          </label>
          <label className="field">
            <span>Benchmark ETF</span>
            <input className="input" value={draft.benchmark_symbol} onChange={(e) => setDraft({ ...draft, benchmark_symbol: e.target.value.toUpperCase() })} />
          </label>
        </div>
        <fieldset className="rules">
          <legend>Event reminders</legend>
          {draft.reminder_rules.map((rule, index) => (
            <div key={index} className="rule-row">
              <input
                className="input input-num"
                type="number"
                min={0}
                max={365}
                aria-label="Offset"
                value={rule.offset}
                onChange={(e) => setRule(index, { offset: Number(e.target.value) })}
              />
              <select className="input" aria-label="Unit" value={rule.unit} onChange={(e) => setRule(index, { unit: e.target.value as "calendar" | "trading" })}>
                <option value="calendar">calendar days</option>
                <option value="trading">trading sessions (XNYS)</option>
              </select>
              <span className="muted">before the provider date</span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setDraft({ ...draft, reminder_rules: draft.reminder_rules.filter((_, i) => i !== index) })}
              >
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setDraft({ ...draft, reminder_rules: [...draft.reminder_rules, { offset: 7, unit: "calendar" }] })}
          >
            <Plus aria-hidden size={14} /> Add reminder
          </button>
        </fieldset>
        <div className="form-actions">
          <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
            Save monitoring settings
          </button>
          {message && <span role="status">{message}</span>}
        </div>
      </div>
    </details>
  );
}
