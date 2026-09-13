import { dateTime, shortDate } from "../format";
import type { ScanRun } from "../types";

export function SummaryCards({ run }: { run: ScanRun }) {
  const s = run.summary;
  const cards = [
    { label: "Companies evaluated", value: s.evaluated, tone: "neutral" },
    { label: "Qualifying", value: s.qualifying, tone: "pass" },
    { label: "Does not qualify", value: s.failed, tone: "fail" },
    { label: "Insufficient data", value: s.insufficient_data, tone: "unknown" },
  ];
  return (
    <section aria-label="Scan summary">
      <div className="summary-grid">
        {cards.map((card) => (
          <div key={card.label} className={`summary-card summary-${card.tone}`}>
            <span className="summary-value">{card.value}</span>
            <span className="summary-label">{card.label}</span>
          </div>
        ))}
      </div>
      <p className="summary-meta">
        {run.mode === "demo" ? "Demo scan" : "Live scan"} finished {dateTime(run.finished_at)} · prices as of{" "}
        {shortDate(run.latest_completed_session)} close · {s.catalyst_records} catalyst records
        {s.undated_excluded > 0 && ` · ${s.undated_excluded} undated excluded`}
      </p>
    </section>
  );
}
