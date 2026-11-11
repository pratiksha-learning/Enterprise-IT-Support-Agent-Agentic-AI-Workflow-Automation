import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { PendingApproval } from "../types";
import { Card, Empty, ErrorBanner, fmt } from "../components/common";

export default function ApprovalsPage() {
  const [items, setItems] = useState<PendingApproval[]>([]);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => api.pendingApprovals().then(setItems).catch(setError), []);
  useEffect(() => { load(); }, [load]);

  async function decide(id: string, decision: "approve" | "reject") {
    setError(null); setMessage(null);
    try {
      const r = await api.decide(id, decision, comments[id]);
      setMessage(`${r.request_number} is now ${r.status.replace(/_/g, " ")}`);
      await load();
    } catch (e) { setError(e); }
  }

  return (
    <Card title="Approval inbox (local mock of Power Automate Approvals in Teams)" actions={<button className="ghost" onClick={load}>Refresh</button>}>
      <ErrorBanner error={error} />
      {message && <div className="ok-banner">{message}</div>}
      {items.length === 0 ? <Empty>No approvals waiting for you.</Empty> : items.map(({ approval, request }) => (
        <div className="approval" key={approval.id}>
          <div>
            <h3>{request.request_number}: {request.requester_upn}</h3>
            <p><b>{request.application_id}</b> / <b>{request.role}</b> for {request.duration_days} days
              {request.privileged && <span className="badge badge-warn">privileged</span>}</p>
            <p className="justification">“{request.justification}”</p>
            <p className="muted small">Stage {approval.stage} of {request.required_stages} · you were selected as <b>{approval.approver_reason.replace(/_/g, " ")}</b> · due {fmt(approval.due_at)}</p>
          </div>
          <div className="approval-actions">
            <input placeholder="Comment (optional)" value={comments[approval.id] ?? ""} onChange={(e) => setComments({ ...comments, [approval.id]: e.target.value })} />
            <button onClick={() => decide(approval.id, "approve")}>Approve</button>
            <button className="danger" onClick={() => decide(approval.id, "reject")}>Reject</button>
          </div>
        </div>
      ))}
    </Card>
  );
}
