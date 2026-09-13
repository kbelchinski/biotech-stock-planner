import { CircleAlert, Download, LoaderCircle, Play } from "lucide-react";
import { api } from "../api";
import { shortDate } from "../format";
import type { ScanJob, StatusResponse } from "../types";

interface Props {
  status: StatusResponse;
  job: ScanJob | null;
  validation: string[];
  scenario: string;
  onScenarioChange: (scenario: string) => void;
  onRun: () => void;
  runError: string | null;
  exportScanId: string | null;
}

export function ScanControls({ status, job, validation, scenario, onScenarioChange, onRun, runError, exportScanId }: Props) {
  const running = job !== null;
  const percent = job ? Math.max(6, Math.round((job.step / Math.max(job.total_steps, 1)) * 100)) : 0;
  const scenarioInfo = status.demo_scenarios.find((s) => s.id === scenario);

  return (
    <div className="panel scan-controls">
      <div className="scan-row">
        <div className="scan-intro">
          <h2>Run scan</h2>
          <p>
            {status.mode === "demo"
              ? "Runs the full pipeline against synthetic provider responses."
              : "Queries BPIQ Apex catalysts and Alpaca SIP daily bars."}{" "}
            Scan date {shortDate(status.scan_date)} (New York).
          </p>
        </div>
        <div className="scan-actions">
          {status.mode === "demo" && (
            <label className="select-field">
              <span>Scenario</span>
              <select value={scenario} onChange={(e) => onScenarioChange(e.target.value)} disabled={running}>
                {status.demo_scenarios.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {exportScanId && (
            <a className="btn btn-secondary" href={api.exportUrl(exportScanId)} download>
              <Download aria-hidden size={16} /> Export CSV
            </a>
          )}
          <button type="button" className="btn btn-primary btn-run" onClick={onRun} disabled={running || validation.length > 0}>
            {running ? <LoaderCircle aria-hidden size={16} className="spin" /> : <Play aria-hidden size={16} />}
            {running ? "Scanning…" : "Run scan"}
          </button>
        </div>
      </div>

      {status.mode === "demo" && scenarioInfo && scenario !== "normal" && (
        <p className="scenario-note">Simulated scenario: {scenarioInfo.description}</p>
      )}

      {validation.length > 0 && (
        <ul className="validation" role="alert">
          {validation.map((message) => (
            <li key={message}>
              <CircleAlert aria-hidden size={14} /> {message}
            </li>
          ))}
        </ul>
      )}

      {status.live_configuration_problems.length > 0 && (
        <div className="alert alert-error" role="alert">
          <strong>Live mode is not configured.</strong> {status.live_configuration_problems.join(" ")} Add credentials to
          <code>.env</code> and restart the backend.
        </div>
      )}

      <div className="progress" role="status" aria-live="polite" hidden={!running}>
        <div className="progress-track">
          <div className="progress-bar" style={{ width: `${percent}%` }} />
        </div>
        <span className="progress-label">
          {job ? `Step ${job.step} of ${job.total_steps}: ${job.message}` : ""}
        </span>
      </div>

      {runError && (
        <div className="alert alert-error" role="alert">
          <strong>Scan could not run.</strong> {runError}
        </div>
      )}
    </div>
  );
}
