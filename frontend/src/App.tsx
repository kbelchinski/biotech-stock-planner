import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, researchApi } from "./api";
import type { ScanCriteria, ScanJob, ScanRun, StatusResponse } from "./types";
import { AppHeader } from "./components/AppHeader";
import { FilterPanel, validateCriteria } from "./components/FilterPanel";
import { ScanControls } from "./components/ScanControls";
import { ScanBanners } from "./components/ScanBanners";
import { SummaryCards } from "./components/SummaryCards";
import { ResultsTable } from "./components/ResultsTable";
import { CompanyDrawer } from "./components/CompanyDrawer";
import { EmptyState, LoadError, LoadingScreen, ResultsSkeleton } from "./components/States";
import { NavBar } from "./components/NavBar";
import { useRoute } from "./router";
import { WatchlistPage } from "./pages/WatchlistPage";
import { ResearchPage } from "./pages/ResearchPage";
import { PlansPage } from "./pages/PlansPage";
import { JournalPage } from "./pages/JournalPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";

const SUCCESSFUL_OUTCOMES = new Set(["success", "no_matches"]);
const POLL_MS = 600;
const NOTIFICATION_POLL_MS = 60_000;
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function withoutRunwayFilter(criteria: ScanCriteria): ScanCriteria {
  return { ...criteria, runway_enabled: false };
}

export default function App() {
  const route = useRoute();
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
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [unread, setUnread] = useState(0);
  const mounted = useRef(true);

  const loadResearchMeta = useCallback(async () => {
    try {
      const [list, notes] = await Promise.all([researchApi.watchlist(), researchApi.notifications()]);
      if (!mounted.current) return;
      setWatched(new Set(list.items.map((i) => i.ticker)));
      setUnread(notes.unread);
    } catch {
      // The screener keeps working if research endpoints are unavailable.
    }
  }, []);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [nextStatus, latest] = await Promise.all([api.status(), api.latest()]);
      if (!mounted.current) return;
      setStatus(nextStatus);
      setCriteria((current) => withoutRunwayFilter(current ?? latest.latest_successful?.criteria ?? nextStatus.defaults));
      setLastSuccessful(latest.latest_successful);
      setAttempt(latest.latest_attempt);
      void loadResearchMeta();
    } catch (error) {
      if (mounted.current) setLoadError(errorMessage(error));
    }
  }, [loadResearchMeta]);

  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = window.setInterval(() => void loadResearchMeta(), NOTIFICATION_POLL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [load, loadResearchMeta]);

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

  const toggleWatch = async (ticker: string, name: string | null) => {
    try {
      if (watched.has(ticker)) await researchApi.unwatch(ticker);
      else await researchApi.watch(ticker, undefined, name);
      await loadResearchMeta();
    } catch (error) {
      setRunError(errorMessage(error));
    }
  };

  if (!status || !criteria) {
    return loadError ? <LoadError message={loadError} onRetry={load} /> : <LoadingScreen />;
  }

  const showingAttempt = Boolean(attempt && viewAttempt && attempt.results.length > 0);
  const displayed = showingAttempt ? attempt : lastSuccessful;
  const selected = displayed?.results.find((r) => r.ticker === selectedTicker) ?? null;
  const running = job !== null;
  const demoScenario = status.mode === "demo" ? scenario : null;

  return (
    <div className="app">
      <a href={route.page === "screener" ? "#results" : "#main"} className="skip-link" onClick={(e) => {
        e.preventDefault();
        document.getElementById(route.page === "screener" ? "results" : "main")?.focus();
      }}>
        Skip to content
      </a>
      <AppHeader status={status} lastSuccessful={lastSuccessful} />
      <NavBar route={route} unread={unread} onUnreadChange={setUnread} />
      {route.page === "screener" ? (
        <main className="layout">
          <aside className="sidebar" aria-label="Screening criteria">
            <FilterPanel
              criteria={criteria}
              defaults={withoutRunwayFilter(status.defaults)}
              status={status}
              disabled={running}
              onChange={(next) => setCriteria(withoutRunwayFilter(next))}
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
      ) : (
        <main className="page" id="main" tabIndex={-1}>
          {route.page === "watchlist" && (
            <WatchlistPage status={status} scenario={scenario} onScenarioChange={setScenario} onChanged={loadResearchMeta} />
          )}
          {route.page === "research" && (
            <ResearchPage ticker={route.param} mode={status.mode} scenario={demoScenario} onWatchChanged={loadResearchMeta} />
          )}
          {route.page === "plans" && <PlansPage initialTicker={route.param} scenario={demoScenario} today={status.scan_date} />}
          {route.page === "journal" && <JournalPage scenario={demoScenario} />}
          {route.page === "integrations" && <IntegrationsPage query={route.query} />}
        </main>
      )}
      {route.page === "screener" && selected && displayed && (
        <CompanyDrawer
          result={selected}
          run={displayed}
          watched={watched.has(selected.ticker)}
          onToggleWatch={() => toggleWatch(selected.ticker, selected.name)}
          onClose={() => setSelectedTicker(null)}
        />
      )}
    </div>
  );
}
