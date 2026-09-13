import { useEffect, useId, useState, type ReactNode } from "react";
import { RotateCcw, SlidersHorizontal } from "lucide-react";
import { addDays, shortDate } from "../format";
import type { ScanCriteria, StatusResponse } from "../types";

interface Props {
  criteria: ScanCriteria;
  defaults: ScanCriteria;
  status: StatusResponse;
  disabled: boolean;
  onChange: (criteria: ScanCriteria) => void;
}

export function validateCriteria(c: ScanCriteria): string[] {
  const errors: string[] = [];
  const numbers: [number, string][] = [
    [c.catalyst_min_days, "catalyst window start"],
    [c.catalyst_max_days, "catalyst window end"],
    [c.market_cap_min_usd, "market cap minimum"],
    [c.market_cap_max_usd, "market cap maximum"],
    [c.price_above_usd, "price threshold"],
    [c.runway_min_months, "runway minimum"],
    [c.liquidity_min_avg_weekly_usd, "turnover threshold"],
  ];
  for (const [value, label] of numbers) {
    if (!Number.isFinite(value) || value < 0) errors.push(`Enter a valid, non-negative ${label}.`);
  }
  if (c.catalyst_min_days > c.catalyst_max_days) errors.push("Catalyst window start must be on or before its end.");
  if (c.catalyst_max_days > 730) errors.push("Catalyst window cannot extend beyond 730 days.");
  if (!Number.isInteger(c.catalyst_min_days) || !Number.isInteger(c.catalyst_max_days))
    errors.push("Catalyst window must be whole days.");
  if (c.catalyst_types.length === 0) errors.push("Select at least one catalyst type.");
  if (c.market_cap_min_usd > c.market_cap_max_usd) errors.push("Market cap minimum must not exceed the maximum.");
  return [...new Set(errors)];
}

export function FilterPanel({ criteria, defaults, status, disabled, onChange }: Props) {
  const set = <K extends keyof ScanCriteria>(key: K, value: ScanCriteria[K]) => onChange({ ...criteria, [key]: value });
  const windowStart = addDays(status.scan_date, criteria.catalyst_min_days);
  const windowEnd = addDays(status.scan_date, criteria.catalyst_max_days);
  const trialLimited =
    status.mode === "live" &&
    status.providers.bpiq.access_tier === "apex_trial" &&
    criteria.catalyst_max_days > status.providers.bpiq.trial_horizon_days;

  return (
    <form className="panel filters" onSubmit={(e) => e.preventDefault()} aria-labelledby="filters-title">
      <div className="panel-head">
        <h2 id="filters-title">
          <SlidersHorizontal aria-hidden size={16} /> Screening criteria
        </h2>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(defaults)} disabled={disabled}>
          <RotateCcw aria-hidden size={14} /> Defaults
        </button>
      </div>

      <fieldset className="filter-body" disabled={disabled}>
        <Group title="Catalyst" note="Always applied">
          <div className="field-row">
            <NumberField label="From" suffix="days" value={criteria.catalyst_min_days} onChange={(v) => set("catalyst_min_days", v)} />
            <NumberField label="To" suffix="days" value={criteria.catalyst_max_days} onChange={(v) => set("catalyst_max_days", v)} />
          </div>
          <p className="hint">
            {shortDate(windowStart)} – {shortDate(windowEnd)}, inclusive
          </p>
          {trialLimited && (
            <p className="hint hint-warn">
              Your BPIQ Apex trial covers only the next {status.providers.bpiq.trial_horizon_days} days. This window will
              be reported as a subscription limitation.
            </p>
          )}
          <div className="checks" role="group" aria-label="Catalyst types">
            {status.catalyst_types.map((type) => (
              <label key={type.id} className="check">
                <input
                  type="checkbox"
                  checked={criteria.catalyst_types.includes(type.id)}
                  onChange={(e) =>
                    set(
                      "catalyst_types",
                      e.target.checked
                        ? status.catalyst_types.map((t) => t.id).filter((id) => id === type.id || criteria.catalyst_types.includes(id))
                        : criteria.catalyst_types.filter((id) => id !== type.id),
                    )
                  }
                />
                <span>{type.label}</span>
              </label>
            ))}
          </div>
        </Group>

        <Group title="Market cap" enabled={criteria.market_cap_enabled} onToggle={(v) => set("market_cap_enabled", v)}>
          <div className="field-row">
            <NumberField label="Min" prefix="$" suffix="M" step={1} scale={1e6} value={criteria.market_cap_min_usd} onChange={(v) => set("market_cap_min_usd", v)} />
            <NumberField label="Max" prefix="$" suffix="M" step={1} scale={1e6} value={criteria.market_cap_max_usd} onChange={(v) => set("market_cap_max_usd", v)} />
          </div>
          <p className="hint">Inclusive · BPIQ company market cap</p>
        </Group>

        <Group title="Stock price" enabled={criteria.price_enabled} onToggle={(v) => set("price_enabled", v)}>
          <NumberField label="Close above" prefix="$" step={0.01} value={criteria.price_above_usd} onChange={(v) => set("price_above_usd", v)} />
          <p className="hint">Latest completed regular-session close</p>
        </Group>

        <Group title="Cash runway" enabled={criteria.runway_enabled} onToggle={(v) => set("runway_enabled", v)}>
          <NumberField label="At least" suffix="months" step={1} value={criteria.runway_min_months} onChange={(v) => set("runway_min_months", v)} />
          {status.runway.available ? (
            <p className="hint">
              <span className="tag tag-mock">Mock</span> Demo financials. Not from any provider.
            </p>
          ) : (
            <p className="hint hint-warn">{status.runway.note}</p>
          )}
        </Group>

        <Group title="Liquidity" enabled={criteria.liquidity_enabled} onToggle={(v) => set("liquidity_enabled", v)}>
          <NumberField
            label="Avg weekly turnover above"
            prefix="$"
            suffix="M"
            step={0.1}
            scale={1e6}
            value={criteria.liquidity_min_avg_weekly_usd}
            onChange={(v) => set("liquidity_min_avg_weekly_usd", v)}
          />
          <p className="hint">Volume × VWAP, last 4 completed trading weeks, SIP feed</p>
        </Group>

        <Group title="Listing" enabled={criteria.listing_enabled} onToggle={(v) => set("listing_enabled", v)}>
          <p className="hint">Active on a US exchange (NASDAQ, NYSE, AMEX, ARCA, BATS, NYSEARCA); OTC excluded.</p>
        </Group>

        <details className="advanced">
          <summary>Advanced query options</summary>
          <label className="check">
            <input
              type="checkbox"
              checked={criteria.include_near_term_catalysts}
              onChange={(e) => set("include_near_term_catalysts", e.target.checked)}
            />
            <span>
              Include catalysts before the window
              <small>Shows near-term events as timing failures. Uses more BPIQ requests.</small>
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={criteria.provider_market_cap_prefilter}
              onChange={(e) => set("provider_market_cap_prefilter", e.target.checked)}
            />
            <span>
              Filter market cap at BPIQ
              <small>Fewer requests, but excluded companies aren't counted or explained.</small>
            </span>
          </label>
        </details>
      </fieldset>
    </form>
  );
}

interface GroupProps {
  title: string;
  children: ReactNode;
  enabled?: boolean;
  onToggle?: (value: boolean) => void;
  note?: string;
}

function Group({ title, children, enabled = true, onToggle, note }: GroupProps) {
  const id = useId();
  return (
    <section className={`filter-group${enabled ? "" : " is-off"}`} aria-labelledby={id}>
      <div className="filter-group-head">
        <h3 id={id}>{title}</h3>
        {onToggle ? (
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label={`Apply ${title.toLowerCase()} criterion`}
            className="switch"
            onClick={() => onToggle(!enabled)}
          >
            <span className="switch-thumb" />
          </button>
        ) : (
          <span className="tag">{note}</span>
        )}
      </div>
      {!enabled && <p className="off-label">Not applied</p>}
      <div className="filter-group-body" inert={!enabled ? true : undefined}>
        {children}
      </div>
    </section>
  );
}

interface NumberFieldProps {
  label: string;
  value: number;
  onChange: (value: number) => void;
  prefix?: string;
  suffix?: string;
  step?: number;
  scale?: number;
}

function NumberField({ label, value, onChange, prefix, suffix, step = 1, scale = 1 }: NumberFieldProps) {
  const id = useId();
  const display = (v: number) => (Number.isFinite(v) ? String(Number((v / scale).toFixed(6))) : "");
  const [text, setText] = useState(display(value));

  useEffect(() => {
    const parsed = text.trim() === "" ? NaN : Number(text) * scale;
    if (!(Number.isNaN(parsed) && Number.isNaN(value)) && Math.abs(parsed - value) > 1e-9) setText(display(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-wrap">
        {prefix && <span className="affix">{prefix}</span>}
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min={0}
          step={step}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            onChange(e.target.value.trim() === "" ? NaN : Number(e.target.value) * scale);
          }}
        />
        {suffix && <span className="affix">{suffix}</span>}
      </div>
    </div>
  );
}
