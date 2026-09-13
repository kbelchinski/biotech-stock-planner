import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import type { ScanCriteria, ScanJob, ScanRun, StatusResponse } from "./types";
import { AppHeader } from "./components/AppHeader";
import { FilterPanel, validateCriteria } from "./components/FilterPanel";
import { ScanControls } from "./components/ScanControls";
import { ScanBanners } from "./components/ScanBanners";
import { SummaryCards } from "./components/SummaryCards";
import { ResultsTable } from "./components/ResultsTable";
import { CompanyDrawer } from "./components/CompanyDrawer";
import { EmptyState, LoadError, LoadingScreen, ResultsSkeleton } from "./components/States";

const SUCCESSFUL_OUTCOMES = new Set(["success", "no_matches"]);
const POLL_MS = 600;
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default function App() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [criteria, setCriteria] = useState<ScanCriteria | null>(null);
  const [scenario, setScenario] = useState("normal");
  const [lastSuccessful, setLastSuccessful] = useState<ScanRun | null>(null);
  const [attempt, setAttempt] = useState<ScanRun | null>(null);
  const [viewAttempt, setViewAttempt] = useState(true);
  const [job, setJob] = useState<ScanJob | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [nextStatus, latest] = await Promise.all([api.status(), api.latest()]);
      if (!mounted.current) return;
      setStatus(nextStatus);
      setCriteria((current) => current ?? latest.latest_successful?.criteria ?? nextStatus.defaults);
      setLastSuccessful(latest.latest_successful);
      setAttempt(latest.latest_attempt);
    } catch (error) {
      if (mounted.current) setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  const runScan = async () => {
    if (!criteria || !status) return;
    setRunError(null);
    try {
      let current = await api.startScan(criteria, status.mode === "demo" ? scenario : null);
      setJob(current);
      while (current.status === "running") {
        await wait(POLL_MS);
        current = await api.job(current.id);
        if (!mounted.current) return;
        setJob(current);
      }
      if (current.status === "failed" || !current.scan_id) {
        setRunError(current.error ?? "The scan failed.");
        return;
      }
      const run = await api.scan(current.scan_id);
      if (!mounted.current) return;
      if (SUCCESSFUL_OUTCOMES.has(run.outcome)) {
        setLastSuccessful(run);
        setAttempt(null);
      } else {
        setAttempt(run);
        setViewAttempt(run.results.length > 0);
      }
      setSelectedTicker(null);
    } catch (error) {
      if (mounted.current) setRunError(errorMessage(error));
    } finally {
      if (mounted.current) setJob(null);
    }
  };

  if (!status || !criteria) {
    return loadError ? <LoadError message={loadError} onRetry={load} /> : <LoadingScreen />;
  }

  const showingAttempt = Boolean(attempt && viewAttempt && attempt.results.length > 0);
  const displayed = showingAttempt ? attempt : lastSuccessful;
  const selected = displayed?.results.find((r) => r.ticker === selectedTicker) ?? null;
  const running = job !== null;

  return (
    <div className="app">
      <a href="#results" className="skip-link">
        Skip to results
      </a>
      <AppHeader status={status} lastSuccessful={lastSuccessful} />
      <main className="layout">
        <aside className="sidebar" aria-label="Screening criteria">
          <FilterPanel
            criteria={criteria}
            defaults={status.defaults}
            status={status}
            disabled={running}
            onChange={setCriteria}
          />
        </aside>
        <section className="content">
          <ScanControls
            status={status}
            job={job}
            validation={validateCriteria(criteria)}
            scenario={scenario}
            onScenarioChange={setScenario}
            onRun={runScan}
            runError={runError}
            exportScanId={displayed?.id ?? null}
          />
          <ScanBanners
            attempt={attempt}
            lastSuccessful={lastSuccessful}
            displayed={displayed}
            showingAttempt={showingAttempt}
            onViewAttempt={setViewAttempt}
          />
          {displayed ? (
            <>
              <SummaryCards run={displayed} />
              <ResultsTable run={displayed} selectedTicker={selectedTicker} onSelect={setSelectedTicker} />
            </>
          ) : running ? (
            <ResultsSkeleton />
          ) : (
            <EmptyState mode={status.mode} />
          )}
        </section>
      </main>
      {selected && displayed && (
        <CompanyDrawer result={selected} run={displayed} onClose={() => setSelectedTicker(null)} />
      )}
    </div>
  );
}
