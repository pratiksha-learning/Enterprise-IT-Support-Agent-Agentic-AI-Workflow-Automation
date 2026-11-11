// Thin typed client for the backend REST API.
import type {
  AccessRequest, Application, AuditEntry, ChatResponse, DemoUser, KnowledgeDocument, MetricsSnapshot,
  NotificationItem, PendingApproval, Principal, Ticket,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public correlationId?: string) {
    super(message);
  }
}

let token: string | null = null;
export const setToken = (t: string | null) => { token = t; };

async function request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = json?.error;
    throw new ApiError(res.status, err?.code ?? "HTTP_ERROR", err?.message ?? res.statusText, err?.correlation_id);
  }
  return json as T;
}

const newKey = () => `ui-${crypto.randomUUID()}`;

export const api = {
  demoUsers: () => request<DemoUser[]>("GET", "/api/v1/auth/demo-users"),
  devToken: (userId: string) =>
    request<{ access_token: string; user: Principal }>("POST", "/api/v1/auth/dev-token", { user_id: userId }),
  me: () => request<Principal>("GET", "/api/v1/auth/me"),

  chat: (message: string, conversationId: string | null) =>
    request<ChatResponse>("POST", "/api/v1/agent/chat", { message, conversation_id: conversationId }),

  documents: () => request<KnowledgeDocument[]>("GET", "/api/v1/knowledge/documents"),

  tickets: () => request<{ items: Ticket[]; total: number }>("GET", "/api/v1/tickets"),
  createTicket: (body: { title: string; description: string; category: string; priority: string }) =>
    request<Ticket>("POST", "/api/v1/tickets", body, { "Idempotency-Key": newKey() }),
  updateTicketStatus: (number: string, status: string, comment?: string) =>
    request<Ticket>("PATCH", `/api/v1/tickets/${number}/status`, { status, comment }),

  applications: () => request<Application[]>("GET", "/api/v1/access/applications"),
  accessRequests: () => request<{ items: AccessRequest[]; total: number }>("GET", "/api/v1/access-requests"),
  submitAccess: (body: { application_id: string; role: string; justification: string; duration_days: number }) =>
    request<AccessRequest>("POST", "/api/v1/access-requests", body, { "Idempotency-Key": newKey() }),
  cancelAccess: (ref: string) => request<AccessRequest>("POST", `/api/v1/access-requests/${ref}/cancel`),
  retryProvisioning: (ref: string) => request<AccessRequest>("POST", `/api/v1/access-requests/${ref}/retry-provisioning`),
  accessAudit: (ref: string) => request<AuditEntry[]>("GET", `/api/v1/access-requests/${ref}/audit`),

  pendingApprovals: () => request<PendingApproval[]>("GET", "/api/v1/approvals/pending"),
  decide: (approvalId: string, decision: "approve" | "reject", comment?: string) =>
    request<AccessRequest>("POST", `/api/v1/approvals/${approvalId}/decision`, { decision, comment }),

  audit: () => request<{ items: AuditEntry[]; total: number }>("GET", "/api/v1/audit?limit=200"),
  verifyAudit: () => request<{ valid: boolean; entries_checked: number; first_invalid_id: number | null }>(
    "GET", "/api/v1/audit/verify"),

  notifications: () => request<NotificationItem[]>("GET", "/api/v1/notifications/me"),

  metrics: () => request<MetricsSnapshot>("GET", "/api/v1/admin/metrics"),
  faults: () => request<Record<string, boolean>>("GET", "/api/v1/admin/fault-injection"),
  setFault: (component: string, enabled: boolean) =>
    request<Record<string, boolean>>("POST", "/api/v1/admin/fault-injection", { component, enabled }),
};
