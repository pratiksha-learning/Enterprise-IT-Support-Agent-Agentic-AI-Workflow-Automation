import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { Ticket } from "../types";
import { Badge, Card, Empty, ErrorBanner, fmt } from "../components/common";

const NEXT: Record<string, string[]> = {
  new: ["in_progress", "on_hold", "resolved", "cancelled"],
  in_progress: ["on_hold", "resolved", "cancelled"],
  on_hold: ["in_progress", "cancelled"],
  resolved: ["closed", "in_progress"],
};

export default function TicketsPage({ canManage }: { canManage: boolean }) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [form, setForm] = useState({ title: "", description: "", category: "hardware", priority: "P3" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.tickets().then((r) => setTickets(r.items)).catch(setError), []);
  useEffect(() => { load(); }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      await api.createTicket(form);
      setForm({ ...form, title: "", description: "" });
      await load();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }

  async function move(number: string, status: string) {
    setError(null);
    try { await api.updateTicketStatus(number, status, "Updated from demo UI"); await load(); } catch (err) { setError(err); }
  }

  return (
    <div className="grid-2">
      <Card title="Create ticket">
        <form className="form" onSubmit={create}>
          <label>Title<input required minLength={5} maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          <label>Description<textarea required minLength={10} rows={4} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <div className="row">
            <label>Category
              <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {["hardware", "software", "network", "access", "account", "other"].map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
            <label>Priority
              <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                {["P1", "P2", "P3", "P4"].map((p) => <option key={p}>{p}</option>)}
              </select>
            </label>
          </div>
          <button disabled={busy}>Create ticket</button>
        </form>
        <ErrorBanner error={error} />
      </Card>
      <Card title={canManage ? "All tickets (IT agent view)" : "My tickets"} actions={<button className="ghost" onClick={load}>Refresh</button>}>
        {tickets.length === 0 ? <Empty>No tickets yet.</Empty> : (
          <table>
            <thead><tr><th>Number</th><th>Title</th><th>Priority</th><th>Status</th><th>Requester</th><th>Updated</th>{canManage && <th>Action</th>}</tr></thead>
            <tbody>
              {tickets.map((t) => (
                <tr key={t.ticket_number}>
                  <td><code>{t.ticket_number}</code></td>
                  <td title={t.description}>{t.title}{t.source !== "api" && <span className="muted"> · {t.source}</span>}</td>
                  <td>{t.priority}</td>
                  <td><Badge value={t.status} /></td>
                  <td>{t.requester_id}</td>
                  <td>{fmt(t.updated_at)}</td>
                  {canManage && (
                    <td>
                      {(NEXT[t.status] ?? []).length > 0 && (
                        <select aria-label={`Move ${t.ticket_number}`} value="" onChange={(e) => e.target.value && move(t.ticket_number, e.target.value)}>
                          <option value="">Move to…</option>
                          {(NEXT[t.status] ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
                        </select>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
