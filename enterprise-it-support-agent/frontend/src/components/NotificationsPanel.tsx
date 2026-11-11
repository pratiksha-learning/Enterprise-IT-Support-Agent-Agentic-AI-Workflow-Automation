import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { NotificationItem } from "../types";
import { Badge, fmt } from "./common";

/** Local stand-in for Teams notifications (Flow bot messages) sent to the signed-in persona. */
export default function NotificationsPanel({ refreshKey }: { refreshKey: string }) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.notifications().then((n) => alive && setItems(n)).catch(() => undefined);
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [refreshKey]);

  return (
    <div className="notif">
      <button className="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
        Notifications {items.length > 0 && <span className="count">{items.length}</span>}
      </button>
      {open && (
        <div className="notif-panel" role="dialog" aria-label="Notifications">
          {items.length === 0 ? <p className="empty">No notifications.</p> : items.map((n) => (
            <div key={n.id} className="notif-item">
              <div className="row"><strong>{n.subject}</strong><Badge value={n.status} /></div>
              <p>{n.body}</p>
              <span className="muted small">{n.channel} · {fmt(n.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
