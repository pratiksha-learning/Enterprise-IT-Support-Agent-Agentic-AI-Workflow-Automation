import { useEffect, useState } from "react";
import { api, setToken } from "./services/api";
import type { DemoUser, Principal } from "./types";
import { ErrorBanner } from "./components/common";
import NotificationsPanel from "./components/NotificationsPanel";
import ChatPage from "./pages/ChatPage";
import TicketsPage from "./pages/TicketsPage";
import AccessPage from "./pages/AccessPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import AuditPage from "./pages/AuditPage";
import KnowledgePage from "./pages/KnowledgePage";
import AdminPage from "./pages/AdminPage";

type Page = "chat" | "tickets" | "access" | "approvals" | "knowledge" | "audit" | "admin";

const PAGES: { id: Page; label: string; permission?: string }[] = [
  { id: "chat", label: "Agent chat" },
  { id: "tickets", label: "Tickets" },
  { id: "access", label: "Access requests" },
  { id: "approvals", label: "Approvals", permission: "approvals:decide" },
  { id: "knowledge", label: "Knowledge" },
  { id: "audit", label: "Audit", permission: "audit:read" },
  { id: "admin", label: "Monitoring", permission: "admin:metrics" },
];

export default function App() {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [userId, setUserId] = useState("u-alice");
  const [me, setMe] = useState<Principal | null>(null);
  const [page, setPage] = useState<Page>("chat");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => { api.demoUsers().then(setUsers).catch(setError); }, []);

  useEffect(() => {
    setMe(null);
    (async () => {
      try {
        const t = await api.devToken(userId);
        setToken(t.access_token);
        setMe(await api.me());
        setError(null);
      } catch (e) { setError(e); }
    })();
  }, [userId]);

  const perms = new Set(me?.permissions ?? []);
  const visible = PAGES.filter((p) => !p.permission || perms.has(p.permission));
  // never render a page the current persona is not permitted to use (avoids 403s after switching persona)
  const current: Page = visible.some((p) => p.id === page) ? page : "chat";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>IT</span>
          <div>
            <div className="title">Enterprise IT Support Agent</div>
            <div className="subtitle">Local demo · mock identity, approvals &amp; provisioning (no Microsoft 365 tenant)</div>
          </div>
        </div>
        <div className="row">
          {me && <NotificationsPanel refreshKey={userId} />}
          <label className="persona">Signed in as
            <select value={userId} onChange={(e) => setUserId(e.target.value)}>
              {users.map((u) => <option key={u.id} value={u.id}>{u.display_name} ({u.roles.filter((r) => r !== "employee").join(", ") || "employee"})</option>)}
            </select>
          </label>
        </div>
      </header>
      <nav className="tabs" aria-label="Sections">
        {visible.map((p) => (
          <button key={p.id} className={current === p.id ? "active" : ""} onClick={() => setPage(p.id)}>{p.label}</button>
        ))}
      </nav>
      <main>
        <ErrorBanner error={error} />
        {me && (
          <div key={userId}>
            {current === "chat" && <ChatPage userId={userId} />}
            {current === "tickets" && <TicketsPage canManage={perms.has("tickets:update_status")} />}
            {current === "access" && <AccessPage isAdmin={perms.has("access:admin")} />}
            {current === "approvals" && <ApprovalsPage />}
            {current === "knowledge" && <KnowledgePage />}
            {current === "audit" && <AuditPage />}
            {current === "admin" && <AdminPage />}
          </div>
        )}
      </main>
      <footer className="footer muted small">
        Demo personas are fictional. In mock mode no real access is ever granted. Copilot Studio, Teams, SharePoint and Power Automate
        integrations require tenant configuration – see <code>copilot-studio/deployment-guide.md</code>.
      </footer>
    </div>
  );
}
