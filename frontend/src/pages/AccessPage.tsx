import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { AccessRequest, Application, AuditEntry } from "../types";
import { Badge, Card, Empty, ErrorBanner, fmt } from "../components/common";

export default function AccessPage({ isAdmin }: { isAdmin: boolean }) {
  const [apps, setApps] = useState<Application[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [selected, setSelected] = useState<AccessRequest | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [form, setForm] = useState({ application_id: "", role: "", duration_days: 30, justification: "" });

  const load = useCallback(async () => {
    try {
      const [a, r] = await Promise.all([api.applications(), api.accessRequests()]);
      setApps(a); setRequests(r.items);
    } catch (e) { setError(e); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const app = useMemo(() => apps.find((a) => a.id === form.application_id), [apps, form.application_id]);
  const role = app?.roles.find((r) => r.name === form.role);
  const maxDays = role ? Math.min(role.max_duration_days, app!.max_duration_days) : 365;

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(null);
    try {
      const created = await api.submitAccess(form);
      setForm({ application_id: "", role: "", duration_days: 30, justification: "" });
      await load(); await open(created);
    } catch (err) { setError(err); }
  }

  async function open(r: AccessRequest) {
    setSelected(r);
    try { setAudit(await api.accessAudit(r.request_number)); } catch (e) { setError(e); }
  }

  async function act(fn: () => Promise<AccessRequest>) {
    setError(null);
    try { const r = await fn(); await load(); await open(r); } catch (e) { setError(e); }
  }

  return (
    <div className="grid-2">
      <div>
        <Card title="Request access">
          <form className="form" onSubmit={submit}>
            <label>Application
              <select required value={form.application_id} onChange={(e) => setForm({ ...form, application_id: e.target.value, role: "" })}>
                <option value="">Select…</option>
                {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            {app && (
              <label>Role (least privilege first)
                <select required value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                  <option value="">Select…</option>
                  {[...app.roles].sort((a, b) => a.level - b.level).map((r) => (
                    <option key={r.name} value={r.name}>{r.name} – {r.description}{r.privileged ? " (privileged: 2-stage approval)" : ""}</option>
                  ))}
                </select>
              </label>
            )}
            <label>Duration (days, max {maxDays})
              <input type="number" min={1} max={365} value={form.duration_days} onChange={(e) => setForm({ ...form, duration_days: Number(e.target.value) })} />
            </label>
            <label>Business justification (min 20 chars)
              <textarea required minLength={20} rows={3} value={form.justification} onChange={(e) => setForm({ ...form, justification: e.target.value })} />
            </label>
            <button>Submit for approval</button>
            <p className="muted small">Requester identity comes from your token; approvers are resolved server-side.</p>
          </form>
          <ErrorBanner error={error} />
        </Card>
        {selected && (
          <Card title={`${selected.request_number} – audit history`}>
            <div className="kv">
              <span>Status</span><Badge value={selected.status} />
              <span>Application</span><span>{selected.application_id} / {selected.role}</span>
              <span>Stages</span><span>{selected.current_stage} of {selected.required_stages}</span>
              <span>Expires</span><span>{fmt(selected.access_expires_at)}</span>
              {selected.failure_reason && (<><span>Failure</span><span>{selected.failure_reason}</span></>)}
            </div>
            <ol className="timeline">
              {[...audit].reverse().map((a) => (
                <li key={a.id}><Badge value={a.outcome} /> <code>{a.action}</code> <span className="muted">by {a.actor_id} · {fmt(a.timestamp)}</span></li>
              ))}
            </ol>
          </Card>
        )}
      </div>
      <Card title={isAdmin ? "Access requests (all)" : "My access requests"} actions={<button className="ghost" onClick={load}>Refresh</button>}>
        {requests.length === 0 ? <Empty>No access requests yet.</Empty> : (
          <table>
            <thead><tr><th>Request</th><th>Application / role</th><th>Status</th><th>Approver</th><th /></tr></thead>
            <tbody>
              {requests.map((r) => {
                const pending = r.approvals.find((a) => a.status === "pending");
                return (
                  <tr key={r.id} className={selected?.id === r.id ? "selected" : ""}>
                    <td><button className="link" onClick={() => open(r)}>{r.request_number}</button><div className="muted small">{r.requester_id}</div></td>
                    <td>{r.application_id}<div className="muted small">{r.role}{r.privileged ? " · privileged" : ""}</div></td>
                    <td><Badge value={r.status} /></td>
                    <td>{pending ? `${pending.approver_upn} (stage ${pending.stage})` : r.approvals.at(-1)?.decided_by_id ?? "—"}</td>
                    <td>
                      {r.status === "pending_approval" && <button className="ghost small" onClick={() => act(() => api.cancelAccess(r.request_number))}>Cancel</button>}
                      {isAdmin && r.status === "provisioning_failed" && <button className="small" onClick={() => act(() => api.retryProvisioning(r.request_number))}>Retry</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
