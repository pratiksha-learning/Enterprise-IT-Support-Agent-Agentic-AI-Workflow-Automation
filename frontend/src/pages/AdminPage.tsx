import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { MetricsSnapshot } from "../types";
import { Card, ErrorBanner } from "../components/common";

const sum = (m: MetricsSnapshot | null, name: string, pred: (l: Record<string, string>) => boolean = () => true) =>
  (m?.counters[name] ?? []).filter((c) => pred(c.labels)).reduce((a, c) => a + c.value, 0);

export default function AdminPage() {
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [faults, setFaults] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const [m, f] = await Promise.all([api.metrics(), api.faults()]);
      setMetrics(m); setFaults(f);
    } catch (e) { setError(e); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  const toolOk = sum(metrics, "tool_invocations", (l) => l.outcome === "success");
  const toolFail = sum(metrics, "tool_invocations", (l) => l.outcome === "failure");
  const tiles = [
    { label: "API requests", value: sum(metrics, "http_requests") },
    { label: "Agent turns", value: sum(metrics, "agent_executions") },
    { label: "Tool success rate", value: toolOk + toolFail ? `${Math.round((100 * toolOk) / (toolOk + toolFail))}%` : "—" },
    { label: "Approvals completed", value: sum(metrics, "approvals_completed") },
    { label: "Blocked decisions", value: sum(metrics, "approvals_blocked") },
    { label: "Downstream retries", value: sum(metrics, "retries") },
    { label: "Errors", value: sum(metrics, "errors") },
    { label: "RAG fallbacks", value: sum(metrics, "knowledge_queries", (l) => l.outcome === "fallback") },
  ];

  return (
    <div>
      <ErrorBanner error={error} />
      <div className="tiles">{tiles.map((t) => <div className="tile" key={t.label}><div className="tile-value">{t.value}</div><div className="tile-label">{t.label}</div></div>)}</div>
      <div className="grid-2">
        <Card title="Fault injection (mock adapters only)">
          <p className="muted">Simulate downstream outages to demonstrate retries, failure states and fallbacks.</p>
          {Object.entries(faults).map(([component, on]) => (
            <label key={component} className="toggle">
              <input type="checkbox" checked={on} onChange={async (e) => { try { setFaults(await api.setFault(component, e.target.checked)); } catch (err) { setError(err); } }} />
              {component} outage
            </label>
          ))}
        </Card>
        <Card title="Latency (in-process, since start)">
          <table>
            <thead><tr><th>Metric</th><th>Labels</th><th>Count</th><th>p50</th><th>p95</th></tr></thead>
            <tbody>
              {Object.entries(metrics?.histograms ?? {}).flatMap(([name, series]) => series.map((s, i) => (
                <tr key={`${name}${i}`}><td><code>{name}</code></td><td className="small">{Object.values(s.labels).join(" ")}</td><td>{s.count}</td><td>{s.p50}</td><td>{s.p95}</td></tr>
              )))}
            </tbody>
          </table>
        </Card>
      </div>
      <Card title="Tool invocations">
        <table>
          <thead><tr><th>Tool</th><th>Outcome</th><th>Count</th></tr></thead>
          <tbody>{(metrics?.counters.tool_invocations ?? []).map((c, i) => <tr key={i}><td><code>{c.labels.tool}</code></td><td>{c.labels.outcome}</td><td>{c.value}</td></tr>)}</tbody>
        </table>
      </Card>
    </div>
  );
}
