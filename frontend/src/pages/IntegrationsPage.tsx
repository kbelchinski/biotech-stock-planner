import { useCallback, useEffect, useState } from "react";
import { CircleCheck, CircleHelp, LoaderCircle, PlugZap, ShieldOff } from "lucide-react";
import { errorMessage, researchApi } from "../api";
import { dateTime } from "../format";
import type { IntegrationsStatus, McpStatus } from "../researchTypes";
import { ErrorState, Loading, Notes, Panel } from "../components/research/ui";

interface Props {
  query: URLSearchParams;
}

function Status({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`badge badge-${ok ? "pass" : "unknown"} badge-sm`}>
      {ok ? <CircleCheck aria-hidden size={12} /> : <CircleHelp aria-hidden size={12} />} {label}
    </span>
  );
}

export function IntegrationsPage({ query }: Props) {
  const [data, setData] = useState<IntegrationsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await researchApi.integrations());
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const callback = query.get("mcp");

  return (
    <div className="page-stack">
      {callback === "connected" && (
        <div className="alert alert-success" role="status">
          BPIQ MCP connected. Run discovery to list the tools your account can use.
        </div>
      )}
      {callback === "error" && <ErrorState message={`BPIQ MCP connection failed: ${query.get("message") ?? "unknown error"}`} />}
      {error && <ErrorState message={error} onRetry={load} />}
      {!data && !error && <Loading label="Checking integrations…" />}
      {data && (
        <>
          <div className="integration-grid">
            <Panel title="BPIQ Apex REST" subtitle="Catalysts and historical catalysts.">
              <Status ok={data.bpiq_rest.configured} label={data.bpiq_rest.configured ? "Key configured" : "Not configured"} />
              <p className="small">Access tier: {data.bpiq_rest.access_tier.replace("_", " ")}</p>
              {data.bpiq_rest.access_tier === "apex_trial" && <p className="small hint-warn">Trial covers catalysts in the next 30 days only.</p>}
              <p className="small muted">Premium-only endpoints (e.g. company financials) are not assumed to be available.</p>
            </Panel>
            <Panel title="Alpaca market data" subtitle="Historical daily bars and asset listing.">
              <Status ok={data.alpaca.configured} label={data.alpaca.configured ? "Keys configured" : "Not configured"} />
              <p className="small">{data.alpaca.plan}</p>
              <p className="small muted">IEX-only volume is never substituted for consolidated volume.</p>
            </Panel>
            <Panel title="Optional AI" subtitle={`${data.ai.provider} · manual trigger only`}>
              <Status ok={data.ai.enabled} label={data.ai.enabled ? `Enabled · ${data.ai.model}` : "Disabled"} />
              <p className="small">
                Budget ${data.ai.monthly_budget_usd.toFixed(2)}/month · estimated spend in {data.ai.month}: ${data.ai.month_spend_usd.toFixed(4)}
              </p>
              <Notes items={data.ai.problems} />
            </Panel>
            <Panel title="Monitoring" subtitle={`Daily at ${data.monitoring.daily_refresh_time_ny} New York`}>
              <Status
                ok={data.monitoring.process_scheduler_enabled && data.monitoring.daily_refresh_enabled}
                label={
                  !data.monitoring.process_scheduler_enabled
                    ? "Scheduler disabled in this backend process"
                    : data.monitoring.daily_refresh_enabled
                      ? "Daily refresh on"
                      : "Daily refresh off"
                }
              />
              <p className="small">{data.monitoring.behaviour}</p>
              <p className="small muted">Last scheduled run: {data.monitoring.last_run ? dateTime(data.monitoring.last_run.ran_at) : "none"}</p>
            </Panel>
            <Panel title="Trading" subtitle="Execution">
              <span className="badge badge-not_applied badge-sm">
                <ShieldOff aria-hidden size={12} /> No order placement
              </span>
              <p className="small">{data.orders}</p>
            </Panel>
          </div>
          <McpPanel status={data.bpiq_mcp} mode={data.mode} onChange={(s) => setData({ ...data, bpiq_mcp: s })} />
        </>
      )}
    </div>
  );
}

function McpPanel({ status, mode, onChange }: { status: McpStatus; mode: string; onChange: (s: McpStatus) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testTool, setTestTool] = useState("");
  const [testArgs, setTestArgs] = useState('{\n  "ticker": "CLDX"\n}');
  const [testOutput, setTestOutput] = useState<string | null>(null);
  const [mapping, setMapping] = useState(status.mapping ?? {});

  useEffect(() => {
    const next = { ...(status.mapping ?? {}) };
    for (const cap of ["financials", "insiders", "funds"] as const) {
      const current = next[cap] ?? { tool: null, confirmed: false };
      const suggested = status.suggestions?.[cap]?.[0];
      if (!current.tool && suggested) next[cap] = { ...current, tool: suggested, confirmed: false };
    }
    setMapping(next);
  }, [status.mapping, status.suggestions]);

  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  if (!status.configured) {
    return (
      <Panel title="BPIQ MCP (optional)" subtitle="Financials, insider transactions and hedge fund holdings.">
        <p className="callout callout-warn">{status.message}</p>
        <p className="small">
          Findings on 2026-09-14: the MCP endpoint requires OAuth consent (scope <code>biopharmiq.read</code>); the REST API key is rejected. Tool lists and
          schemas can only be verified after connecting. Everything else in the app works without MCP.
        </p>
      </Panel>
    );
  }

  const tools = status.tools ?? [];
  return (
    <Panel
      title="BPIQ MCP (optional)"
      subtitle={status.url}
      actions={
        <>
          {!status.connected ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy !== null}
              onClick={() => act("connect", async () => void (window.location.href = (await researchApi.mcpConnect()).authorize_url))}
            >
              <PlugZap aria-hidden size={15} /> Connect (opens BPIQ consent)
            </button>
          ) : (
            <>
              <button type="button" className="btn btn-secondary" disabled={busy !== null} onClick={() => act("discover", async () => onChange(await researchApi.mcpDiscover()))}>
                {busy === "discover" && <LoaderCircle aria-hidden size={15} className="spin" />} Discover tools
              </button>
              {status.auth_method !== "api_key" && (
                <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={() => act("disconnect", async () => onChange(await researchApi.mcpDisconnect()))}>
                  Disconnect
                </button>
              )}
            </>
          )}
        </>
      }
    >
      <Status
        ok={status.connected}
        label={
          status.connected
            ? status.auth_method === "api_key"
              ? "Connected with API key"
              : "Connected"
            : "Not connected"
        }
      />
      {status.message && <p className="small">{status.message}</p>}
      {mode === "demo" && <p className="small hint-warn">{status.demo_note} Switch to live mode to use MCP data on research pages.</p>}
      {status.verification_note && <p className="small muted">{status.verification_note}</p>}
      {error && <ErrorState message={error} />}
      <p className="small">
        Tools discovered: {tools.length} {status.discovered_at && `· ${dateTime(status.discovered_at)}`}
      </p>
      {tools.length > 0 && (
        <>
          <ul className="tool-list">
            {tools.map((t) => (
              <li key={t.name}>
                <details>
                  <summary>
                    <code>{t.name}</code> {t.title && <span className="muted">{t.title}</span>}
                  </summary>
                  {t.description && <p className="small">{t.description}</p>}
                  <pre className="code-block">{JSON.stringify(t.input_schema, null, 2)}</pre>
                  {t.output_schema && <pre className="code-block">{JSON.stringify(t.output_schema, null, 2)}</pre>}
                </details>
              </li>
            ))}
          </ul>

          <h3 className="section-title">Test a tool (read-only call; response shown raw)</h3>
          <div className="form-grid form-grid-2">
            <label className="field">
              <span>Tool</span>
              <select className="input" value={testTool} onChange={(e) => setTestTool(e.target.value)}>
                <option value="">Select…</option>
                {tools.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Arguments (JSON, must match the schema)</span>
              <textarea className="input code-input" rows={4} value={testArgs} onChange={(e) => setTestArgs(e.target.value)} />
            </label>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={!testTool || busy !== null}
            onClick={() =>
              act("test", async () => {
                const args = JSON.parse(testArgs) as Record<string, unknown>;
                const out = await researchApi.mcpTestTool(testTool, args);
                setTestOutput(JSON.stringify(out, null, 2));
              })
            }
          >
            Run test call
          </button>
          {testOutput && <pre className="code-block">{testOutput}</pre>}

          <h3 className="section-title">Capability mapping</h3>
          <p className="small">
            Research pages call a tool only after you confirm it, having checked its schema and a test response. Suggestions are based on tool names only.
          </p>
          <div className="mapping-grid">
            {(["financials", "insiders", "funds"] as const).map((cap) => {
              const entry = mapping[cap] ?? { tool: null, confirmed: false };
              const suggested = status.suggestions?.[cap] ?? [];
              return (
                <fieldset key={cap} className="rules">
                  <legend>{cap === "funds" ? "Hedge fund holdings" : cap === "insiders" ? "Insider transactions" : "Financials"}</legend>
                  <select
                    className="input"
                    value={entry.tool ?? ""}
                    onChange={(e) => setMapping({ ...mapping, [cap]: { tool: e.target.value || null, confirmed: false } })}
                  >
                    <option value="">No tool</option>
                    {tools.map((t) => (
                      <option key={t.name} value={t.name}>
                        {t.name}
                        {suggested.includes(t.name) ? " (name suggests)" : ""}
                      </option>
                    ))}
                  </select>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={entry.confirmed}
                      disabled={!entry.tool}
                      onChange={(e) => setMapping({ ...mapping, [cap]: { ...entry, confirmed: e.target.checked } })}
                    />
                    <span>I inspected the schema and a test response</span>
                  </label>
                  {entry.tool && !entry.confirmed && (
                    <p className="small hint-warn">Selected but not confirmed: research pages will not call this tool until you tick the box and save.</p>
                  )}
                  {entry.tool && entry.confirmed && status.mapping?.[cap]?.confirmed !== true && (
                    <p className="small hint-warn">Unsaved change — select Save mapping.</p>
                  )}
                </fieldset>
              );
            })}
          </div>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy !== null}
            onClick={() =>
              act("mapping", async () =>
                onChange(await researchApi.mcpMapping(Object.fromEntries(Object.entries(mapping).map(([k, v]) => [k, { tool: v.tool, confirmed: v.confirmed }])))),
              )
            }
          >
            Save mapping
          </button>
        </>
      )}
    </Panel>
  );
}
