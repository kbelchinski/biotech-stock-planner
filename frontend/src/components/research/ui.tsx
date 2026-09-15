import type { ReactNode } from "react";
import { CircleAlert, ExternalLink, Inbox, RefreshCw } from "lucide-react";
import { dateTime, shortDate } from "../../format";
import type { SourceRef } from "../../types";

export type Kind = "fact" | "calculation" | "user_assumption" | "ai";

const KIND_LABEL: Record<Kind, string> = {
  fact: "Fact",
  calculation: "Calculation",
  user_assumption: "Your assumption",
  ai: "AI interpretation",
};

const KIND_HELP: Record<Kind, string> = {
  fact: "A value or record as returned by a data provider.",
  calculation: "Derived deterministically by this app from provider records.",
  user_assumption: "Something you entered.",
  ai: "Generated text. Verify against the cited evidence.",
};

export function KindBadge({ kind }: { kind: Kind }) {
  return (
    <span className={`kind kind-${kind}`} title={KIND_HELP[kind]}>
      {KIND_LABEL[kind]}
    </span>
  );
}

export function KindLegend() {
  return (
    <p className="kind-legend">
      {(Object.keys(KIND_LABEL) as Kind[]).map((kind) => (
        <span key={kind}>
          <KindBadge kind={kind} /> {KIND_HELP[kind]}
        </span>
      ))}
    </p>
  );
}

interface PanelProps {
  title: string;
  id?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Panel({ title, id, subtitle, actions, children, className }: PanelProps) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={`panel rpanel${className ? ` ${className}` : ""}`} id={id} aria-labelledby={headingId}>
      <header className="rpanel-head">
        <div>
          <h2 id={headingId}>{title}</h2>
          {subtitle && <p className="rpanel-sub">{subtitle}</p>}
        </div>
        {actions && <div className="rpanel-actions">{actions}</div>}
      </header>
      <div className="rpanel-body">{children}</div>
    </section>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="rstate" role="status" aria-live="polite">
      <div className="loader" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="alert alert-error" role="alert">
      <CircleAlert aria-hidden size={18} />
      <div className="alert-body">
        <span>{message}</span>
        {onRetry && (
          <div className="alert-actions">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
              <RefreshCw aria-hidden size={14} /> Retry
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rempty">
      <Inbox aria-hidden size={22} />
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Notes({ items, tone = "warn" }: { items: string[]; tone?: "warn" | "info" | "error" }) {
  if (items.length === 0) return null;
  return (
    <ul className={`notes notes-${tone}`}>
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

export function SourceLine({ source, fallback }: { source: Partial<SourceRef> | null | undefined; fallback?: string }) {
  if (!source) return fallback ? <span className="source-line">{fallback}</span> : null;
  const time = source.source_timestamp
    ? `source time ${source.source_timestamp.length > 10 ? dateTime(source.source_timestamp) : shortDate(source.source_timestamp)}`
    : source.retrieved_at
      ? `retrieved ${dateTime(source.retrieved_at)}`
      : "no timestamp";
  return (
    <span className="source-line">
      {source.provider}
      {source.is_mock && <span className="tag tag-mock">Mock</span>} · {time}
      {source.url && (
        <>
          {" · "}
          <a href={source.url} target="_blank" rel="noreferrer noopener" className="link">
            Source <ExternalLink aria-hidden size={11} />
          </a>
        </>
      )}
    </span>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {hint && <p className="stat-hint">{hint}</p>}
    </div>
  );
}
