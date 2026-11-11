import { Fragment, type ReactNode } from "react";
import { ApiError } from "../services/api";

const STATUS_TONE: Record<string, string> = {
  new: "info", in_progress: "info", on_hold: "warn", resolved: "ok", closed: "muted", cancelled: "muted",
  pending_approval: "warn", pending: "warn", approved: "ok", provisioning: "info", provisioned: "ok",
  rejected: "bad", failed: "bad", provisioning_failed: "bad", expired: "muted",
  success: "ok", failure: "bad", denied: "bad", sent: "ok",
};

export function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${STATUS_TONE[value] ?? "muted"}`}>{value.replace(/_/g, " ")}</span>;
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div className="error-banner" role="alert">
      <strong>{e.code ?? "Error"}</strong>: {e.message}
      {e.correlationId && <span className="muted"> · correlation id {e.correlationId}</span>}
    </div>
  );
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          {title && <h2>{title}</h2>}
          <div>{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export const fmt = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");

/** Very small, safe renderer for agent replies: line breaks, "- " bullets and **bold** (no HTML injection). */
export function RichText({ text }: { text: string }) {
  const inline = (line: string) =>
    line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>);
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) out.push(<ul key={`ul${out.length}`}>{bullets.map((b, i) => <li key={i}>{inline(b)}</li>)}</ul>);
    bullets = [];
  };
  lines.forEach((line, i) => {
    if (line.startsWith("- ")) bullets.push(line.slice(2));
    else {
      flush();
      if (line.trim()) out.push(<p key={`p${i}`}>{inline(line)}</p>);
    }
  });
  flush();
  return <>{out}</>;
}
