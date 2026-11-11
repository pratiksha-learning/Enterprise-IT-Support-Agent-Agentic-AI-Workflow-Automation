import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { KnowledgeDocument } from "../types";
import { Card, ErrorBanner, fmt } from "../components/common";

export default function KnowledgePage() {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api.documents().then(setDocs).catch(setError); }, []);

  return (
    <Card title="Knowledge sources visible to you (security-trimmed)">
      <ErrorBanner error={error} />
      <p className="muted">Documents are filtered by your group membership <i>before</i> retrieval, mirroring SharePoint permissions. Switch persona to see a different set.</p>
      <table>
        <thead><tr><th>Title</th><th>Source</th><th>Allowed groups</th><th>Sensitivity</th><th>Injection flags</th><th>Ingested</th></tr></thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>{d.title}</td>
              <td><code>{d.source_uri}</code></td>
              <td>{d.allowed_groups.join(", ")}</td>
              <td>{d.sensitivity}</td>
              <td>{d.injection_flags > 0 ? <span className="badge badge-bad">{d.injection_flags} neutralised</span> : "0"}</td>
              <td>{fmt(d.ingested_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
