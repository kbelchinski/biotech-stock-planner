import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Calculator, Plus, Save, Trash2 } from "lucide-react";
import { errorMessage, researchApi } from "../api";
import { parseNumber, pct, price, shortDate, usd } from "../format";
import { href } from "../router";
import type { Trade, TradePlanInput, TradePlanResult } from "../researchTypes";
import { CatalystTimeline } from "../components/research/CatalystTimeline";
import { Empty, ErrorState, KindBadge, Loading, Notes, Panel, Stat } from "../components/research/ui";

interface Props {
  initialTicker: string | null;
  scenario: string | null;
  today: string;
}

const EMPTY = {
  ticker: "",
  entry: "",
  stop: "",
  budget: "",
  shares: "",
  capital: "",
  portfolio: "",
  exit: "",
  declines: "10, 25, 50",
  thesis: "",
  invalidation: "",
};

type PlanForm = typeof EMPTY;

function toPlan(f: PlanForm): { plan: TradePlanInput | null; errors: string[] } {
  const errors: string[] = [];
  const ticker = f.ticker.trim().toUpperCase();
  if (!/^[A-Z.]{1,10}$/.test(ticker)) errors.push("Enter a ticker of 1–10 letters.");
  const num = (text: string, label: string) => {
    const value = parseNumber(text);
    if (value !== null && Number.isNaN(value)) errors.push(`${label} must be a number.`);
    return value === null || Number.isNaN(value) ? null : value;
  };
  const declines = f.declines
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number);
  if (declines.some((d) => !Number.isFinite(d))) errors.push("Decline scenarios must be comma-separated numbers.");
  const plan: TradePlanInput = {
    ticker,
    entry_price: num(f.entry, "Entry price"),
    stop_price: num(f.stop, "Stop price"),
    loss_budget_usd: num(f.budget, "Loss budget"),
    position_shares: num(f.shares, "Shares"),
    capital_allocation_usd: num(f.capital, "Capital allocation"),
    portfolio_value_usd: num(f.portfolio, "Portfolio value"),
    planned_exit_date: f.exit || null,
    decline_scenarios_pct: declines.filter(Number.isFinite),
    thesis: f.thesis,
    invalidation: f.invalidation,
  };
  return { plan: errors.length ? null : plan, errors };
}

export function tradeBody(t: Trade, patch: Partial<Trade> = {}): Record<string, unknown> {
  const merged = { ...t, ...patch };
  return {
    kind: merged.kind,
    ticker: merged.ticker,
    status: merged.status,
    plan: merged.plan,
    catalyst_event_id: merged.catalyst_event_id,
    catalyst_category: merged.catalyst_category,
    entry_date: merged.entry_date,
    entry_price: merged.entry_price,
    planned_exit_date: merged.planned_exit_date,
    exit_date: merged.exit_date,
    exit_price: merged.exit_price,
    shares: merged.shares,
    cost_pct_per_side: merged.cost_pct_per_side,
    slippage_pct_per_side: merged.slippage_pct_per_side,
    thesis: merged.thesis,
    invalidation: merged.invalidation,
    notes: merged.notes,
  };
}

export function netReturn(t: Trade): number | null {
  if (!t.entry_price || !t.exit_price) return null;
  const side = (t.cost_pct_per_side + t.slippage_pct_per_side) / 100;
  return (t.exit_price * (1 - side)) / (t.entry_price * (1 + side)) - 1;
}

export function PlansPage({ initialTicker, scenario, today }: Props) {
  const [form, setForm] = useState<PlanForm>({ ...EMPTY, ticker: initialTicker ?? "" });
  const [result, setResult] = useState<TradePlanResult | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [calcError, setCalcError] = useState<string | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [trades, setTrades] = useState<Trade[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadTrades = useCallback(async () => {
    setLoadError(null);
    try {
      setTrades(await researchApi.trades());
    } catch (e) {
      setLoadError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

  useEffect(() => {
    if (initialTicker) setForm((f) => ({ ...f, ticker: initialTicker }));
  }, [initialTicker]);

  const set = (key: keyof PlanForm) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });

  const calculate = async (event: FormEvent) => {
    event.preventDefault();
    const { plan, errors } = toPlan(form);
    setFormErrors(errors);
    setMessage(null);
    if (!plan) return;
    setCalculating(true);
    setCalcError(null);
    try {
      setResult(await researchApi.calculatePlan(plan, scenario));
    } catch (e) {
      setCalcError(errorMessage(e));
    } finally {
      setCalculating(false);
    }
  };

  const savePlan = async () => {
    const { plan } = toPlan(form);
    if (!plan || !result?.output.valid) return;
    try {
      await researchApi.createTrade({
        kind: "planned",
        status: "planned",
        ticker: plan.ticker,
        plan,
        planned_exit_date: plan.planned_exit_date,
        entry_price: plan.entry_price,
        thesis: plan.thesis,
        invalidation: plan.invalidation,
      });
      setMessage("Saved as a planned trade (research plan only; no position, no order).");
      await loadTrades();
    } catch (e) {
      setMessage(errorMessage(e));
    }
  };

  const out = result?.output;
  const byKind = (kind: Trade["kind"]) => trades?.filter((t) => t.kind === kind) ?? [];

  return (
    <div className="page-stack">
      <Panel
        title="Trade-plan calculator"
        id="calculator"
        subtitle="Research planning only. This app cannot place orders."
      >
        <form className="plan-form" onSubmit={calculate} noValidate>
          <div className="form-grid">
            <Field label="Ticker" value={form.ticker} onChange={set("ticker")} />
            <Field label="Proposed entry price ($)" value={form.entry} onChange={set("entry")} numeric />
            <Field label="Planned exit date" value={form.exit} onChange={set("exit")} type="date" />
            <Field label="Stop / invalidation price ($, optional)" value={form.stop} onChange={set("stop")} numeric />
            <Field label="Intended loss budget ($)" value={form.budget} onChange={set("budget")} numeric />
            <Field label="Proposed shares (optional)" value={form.shares} onChange={set("shares")} numeric />
            <Field label="Or capital allocation ($, optional)" value={form.capital} onChange={set("capital")} numeric />
            <Field label="Portfolio value ($, optional)" value={form.portfolio} onChange={set("portfolio")} numeric />
            <Field label="Decline scenarios (%)" value={form.declines} onChange={set("declines")} />
          </div>
          <div className="form-grid form-grid-2">
            <label className="field">
              <span>Thesis</span>
              <textarea className="input" rows={3} value={form.thesis} onChange={set("thesis")} maxLength={4000} />
            </label>
            <label className="field">
              <span>Invalidation conditions</span>
              <textarea className="input" rows={3} value={form.invalidation} onChange={set("invalidation")} maxLength={4000} />
            </label>
          </div>
          <Notes items={formErrors} tone="error" />
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={calculating}>
              <Calculator aria-hidden size={15} /> Calculate
            </button>
            {out?.valid && (
              <button type="button" className="btn btn-secondary" onClick={savePlan}>
                <Save aria-hidden size={15} /> Save as planned trade
              </button>
            )}
            {form.ticker && (
              <a className="link" href={href("research", form.ticker.toUpperCase())}>
                Open research for {form.ticker.toUpperCase()}
              </a>
            )}
            {message && <span role="status">{message}</span>}
          </div>
        </form>
        {calcError && <ErrorState message={calcError} />}
        {out && (
          <div className="plan-result" aria-live="polite">
            <Notes items={out.errors} tone="error" />
            <Notes items={out.warnings} />
            {out.valid && (
              <>
                <dl className="stat-grid">
                  <Stat label="Planned capital commitment" value={usd(out.capital_commitment_usd)} hint={out.capital_basis ?? "Enter a size to calculate"} />
                  <Stat label="Implied shares" value={out.implied_shares != null ? out.implied_shares.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "—"} />
                  <Stat
                    label="Stop-based position size"
                    value={out.stop_based_shares != null ? `${out.stop_based_shares.toLocaleString()} sh · ${usd(out.stop_based_capital_usd)}` : "—"}
                    hint={out.stop_based_note}
                  />
                  <Stat label="Risk per share (entry − stop)" value={price(out.risk_per_share_usd)} />
                  <Stat label="Portfolio exposure" value={out.portfolio_exposure_pct != null ? `${out.portfolio_exposure_pct.toFixed(2)}%` : "—"} />
                </dl>
                <p className="small">
                  <KindBadge kind="calculation" /> from <KindBadge kind="user_assumption" /> inputs.
                </p>
                {out.loss_scenarios.length > 0 && (
                  <table className="mini-table">
                    <caption className="table-caption">Loss scenarios on planned capital</caption>
                    <thead>
                      <tr>
                        <th scope="col">Price decline</th>
                        <th scope="col" className="num">Loss</th>
                        <th scope="col" className="num">Portfolio impact</th>
                      </tr>
                    </thead>
                    <tbody>
                      {out.loss_scenarios.map((s) => (
                        <tr key={s.decline_pct}>
                          <td>−{s.decline_pct}%</td>
                          <td className="num">{usd(s.loss_usd)}</td>
                          <td className="num">{s.portfolio_impact_pct != null ? `${s.portfolio_impact_pct.toFixed(2)}%` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
            <p className="callout callout-warn">{out.disclaimer}</p>
            <h3 className="section-title">Known catalysts before the planned exit</h3>
            {result?.catalyst_note && <p className="small muted">{result.catalyst_note}</p>}
            {!form.exit ? (
              <p className="muted small">Enter a planned exit date to list catalysts in the holding period.</p>
            ) : result && result.catalysts_before_exit.length === 0 ? (
              <p className="muted small">None from connected sources. Absence here does not rule out events.</p>
            ) : (
              result && <CatalystTimeline items={result.catalysts_before_exit} revisions={[]} today={today} horizon={form.exit} />
            )}
          </div>
        )}
      </Panel>

      <PositionForm onSaved={loadTrades} />

      {loadError && <ErrorState message={loadError} onRetry={loadTrades} />}
      {trades === null && !loadError && <Loading label="Loading trades…" />}
      {trades && (
        <>
          <TradeTable title="Planned trades" subtitle="Research plans. Not positions." trades={byKind("planned")} onChanged={loadTrades} />
          <TradeTable title="Hypothetical trades" subtitle="Trades you did not place, tracked for learning. Reported separately from actual results." trades={byKind("hypothetical")} onChanged={loadTrades} />
          <TradeTable title="Actual positions (entered manually)" subtitle="Only what you record here. Watching a company never creates a position." trades={byKind("actual")} onChanged={loadTrades} />
        </>
      )}
    </div>
  );
}

function Field({ label, value, onChange, type = "text", numeric }: { label: string; value: string; onChange: (e: { target: { value: string } }) => void; type?: string; numeric?: boolean }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input className="input" type={type} inputMode={numeric ? "decimal" : undefined} value={value} onChange={onChange} />
    </label>
  );
}

function PositionForm({ onSaved }: { onSaved: () => void }) {
  const [f, setF] = useState({
    kind: "actual",
    ticker: "",
    status: "open",
    entry_date: "",
    entry_price: "",
    shares: "",
    exit_date: "",
    exit_price: "",
    cost: "0",
    slippage: "0",
    category: "",
    thesis: "",
    notes: "",
  });
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [key]: e.target.value });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const n = (text: string) => (text.trim() === "" ? null : Number(text));
    try {
      await researchApi.createTrade({
        kind: f.kind,
        ticker: f.ticker.trim().toUpperCase(),
        status: f.status,
        entry_date: f.entry_date || null,
        entry_price: n(f.entry_price),
        shares: n(f.shares),
        exit_date: f.status === "closed" ? f.exit_date || null : null,
        exit_price: f.status === "closed" ? n(f.exit_price) : null,
        cost_pct_per_side: n(f.cost) ?? 0,
        slippage_pct_per_side: n(f.slippage) ?? 0,
        catalyst_category: f.category || null,
        thesis: f.thesis,
        notes: f.notes,
      });
      setF({ ...f, ticker: "", entry_date: "", entry_price: "", shares: "", exit_date: "", exit_price: "", thesis: "", notes: "" });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <details className="panel subpanel">
      <summary>
        <Plus aria-hidden size={14} /> Record a hypothetical trade or an actual position
      </summary>
      <form className="subpanel-body" onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            <span>Type</span>
            <select className="input" value={f.kind} onChange={set("kind")}>
              <option value="actual">Actual position (I placed it myself)</option>
              <option value="hypothetical">Hypothetical (not placed)</option>
            </select>
          </label>
          <Field label="Ticker" value={f.ticker} onChange={set("ticker")} />
          <label className="field">
            <span>Status</span>
            <select className="input" value={f.status} onChange={set("status")}>
              <option value="open">Open</option>
              <option value="closed">Closed</option>
            </select>
          </label>
          <Field label="Entry date" value={f.entry_date} onChange={set("entry_date")} type="date" />
          <Field label="Entry price ($)" value={f.entry_price} onChange={set("entry_price")} numeric />
          <Field label="Shares" value={f.shares} onChange={set("shares")} numeric />
          {f.status === "closed" && <Field label="Exit date" value={f.exit_date} onChange={set("exit_date")} type="date" />}
          {f.status === "closed" && <Field label="Exit price ($)" value={f.exit_price} onChange={set("exit_price")} numeric />}
          <Field label="Costs % per side" value={f.cost} onChange={set("cost")} numeric />
          <Field label="Slippage % per side" value={f.slippage} onChange={set("slippage")} numeric />
          <Field label="Catalyst category" value={f.category} onChange={set("category")} />
        </div>
        <div className="form-grid form-grid-2">
          <label className="field">
            <span>Thesis</span>
            <textarea className="input" rows={2} value={f.thesis} onChange={set("thesis")} />
          </label>
          <label className="field">
            <span>Notes</span>
            <textarea className="input" rows={2} value={f.notes} onChange={set("notes")} />
          </label>
        </div>
        {error && <p className="notes-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary btn-sm">
            Save record
          </button>
        </div>
      </form>
    </details>
  );
}

function TradeTable({ title, subtitle, trades, onChanged }: { title: string; subtitle: string; trades: Trade[]; onChanged: () => void }) {
  const [closing, setClosing] = useState<{ id: string; date: string; price: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const close = async (t: Trade) => {
    if (!closing) return;
    setError(null);
    try {
      await researchApi.updateTrade(t.id, tradeBody(t, { status: "closed", exit_date: closing.date || null, exit_price: closing.price ? Number(closing.price) : null }));
      setClosing(null);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const remove = async (t: Trade) => {
    if (!window.confirm(`Delete this ${t.kind} record for ${t.ticker}?`)) return;
    try {
      await researchApi.deleteTrade(t.id);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <Panel title={title} subtitle={subtitle}>
      {error && <p className="notes-error" role="alert">{error}</p>}
      {trades.length === 0 ? (
        <Empty title="None recorded" />
      ) : (
        <div className="mini-table-wrap">
          <table className="mini-table">
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Status</th>
                <th scope="col">Entry</th>
                <th scope="col">Planned exit</th>
                <th scope="col">Exit</th>
                <th scope="col" className="num">Net return</th>
                <th scope="col">Thesis</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td>
                    <a className="ticker link-plain" href={href("research", t.ticker)}>
                      {t.ticker}
                    </a>
                  </td>
                  <td>
                    <span className={`pill pill-${t.status}`}>{t.status.replaceAll("_", " ")}</span>
                  </td>
                  <td>{t.entry_price != null ? `${price(t.entry_price)} · ${shortDate(t.entry_date)}` : t.plan?.entry_price != null ? `plan ${price(t.plan.entry_price)}` : "—"}</td>
                  <td>{shortDate(t.planned_exit_date)}</td>
                  <td>
                    {closing?.id === t.id ? (
                      <span className="inline-close">
                        <input className="input" type="date" aria-label="Exit date" value={closing.date} onChange={(e) => setClosing({ ...closing, date: e.target.value })} />
                        <input className="input input-num" inputMode="decimal" aria-label="Exit price" placeholder="Price" value={closing.price} onChange={(e) => setClosing({ ...closing, price: e.target.value })} />
                        <button type="button" className="btn btn-primary btn-sm" onClick={() => close(t)}>
                          Save
                        </button>
                      </span>
                    ) : t.exit_price != null ? (
                      `${price(t.exit_price)} · ${shortDate(t.exit_date)}`
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="num">{pct(netReturn(t))}</td>
                  <td className="wrap small">{t.thesis || "—"}</td>
                  <td className="nowrap">
                    {t.status === "open" && closing?.id !== t.id && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setClosing({ id: t.id, date: "", price: "" })}>
                        Close
                      </button>
                    )}
                    <button type="button" className="btn btn-ghost btn-sm btn-icon" onClick={() => remove(t)} aria-label={`Delete ${t.ticker} record`}>
                      <Trash2 aria-hidden size={14} />
                    </button>
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
