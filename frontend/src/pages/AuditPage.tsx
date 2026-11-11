import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { AuditEntry } from "../types";
import { Badge, Card, ErrorBanner, fmt } from "../components/common";

export default function AuditPage() {
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [verify, setVerify] = useState<{ valid: boolean; entries_checked: number } | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const [a, v] = await Promise.all([api.audit(), api.verifyAudit()]);
      setItems(a.items); setVerify(v);
    } catch (e) { setError(e); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const shown = items.filter((i) => !filter || `${i.action} ${i.target_id} ${i.actor_id}`.toLowerCase().includes(filter.toLowerCase()));

  return (
    <Card title="Audit trail" actions={
      <div className="row">
        {verify && <span className={`badge ${verify.valid ? "badge-ok" : "badge-bad"}`}>hash chain {verify.valid ? "valid" : "BROKEN"} · {verify.entries_checked} entries</span>}
        <input placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <button className="ghost" onClick={load}>Refresh</button>
      </div>}>
      <ErrorBanner error={error} />
      <table>
        <thead><tr><th>#</th><th>Time</th><th>Action</th><th>Outcome</th><th>Actor</th><th>Target</th><th>Details (redacted)</th><th>Correlation</th></tr></thead>
        <tbody>
          {shown.map((e) => (
            <tr key={e.id}>
              <td>{e.id}</td>
              <td className="nowrap">{fmt(e.timestamp)}</td>
              <td><code>{e.action}</code></td>
              <td><Badge value={e.outcome} /></td>
              <td>{e.actor_id}<div className="muted small">{e.actor_type}</div></td>
              <td>{e.target_type}:{e.target_id}</td>
              <td className="details"><code>{JSON.stringify(e.details)}</code></td>
              <td className="muted small"><code>{e.correlation_id.slice(0, 10)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
