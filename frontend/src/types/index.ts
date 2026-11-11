// API contract types (mirrors backend/app/models/schemas.py)

export interface Principal {
  id: string;
  upn: string;
  display_name: string;
  roles: string[];
  groups: string[];
  auth_type: string;
  permissions?: string[];
}

export interface DemoUser {
  id: string;
  display_name: string;
  upn: string;
  roles: string[];
  title: string;
}

export interface Citation {
  index: number;
  document_id: string;
  title: string;
  source_uri: string;
  heading: string;
  score: number;
  snippet: string;
}

export interface ToolCall {
  tool: string;
  status: "success" | "failure";
  latency_ms: number;
}

export interface ChatResponse {
  conversation_id: string;
  reply: string;
  intent: string;
  tool_calls: ToolCall[];
  citations: Citation[];
  data: Record<string, unknown> | null;
  suggested_replies: string[];
}

export interface Ticket {
  ticket_number: string;
  title: string;
  description: string;
  category: string;
  priority: string;
  status: string;
  requester_id: string;
  requester_upn: string;
  assigned_to: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  id: string;
  stage: number;
  approver_id: string;
  approver_upn: string;
  approver_reason: string;
  status: string;
  provider: string;
  comment: string | null;
  decided_by_id: string | null;
  decision_channel: string | null;
  created_at: string;
  due_at: string;
  decided_at: string | null;
}

export interface AccessRequest {
  id: string;
  request_number: string;
  requester_id: string;
  requester_upn: string;
  application_id: string;
  role: string;
  justification: string;
  duration_days: number;
  status: string;
  privileged: boolean;
  required_stages: number;
  current_stage: number;
  failure_reason: string | null;
  provisioning_reference: string | null;
  access_expires_at: string | null;
  created_at: string;
  updated_at: string;
  approvals: Approval[];
}

export interface ApplicationRole {
  name: string;
  description: string;
  level: number;
  privileged: boolean;
  max_duration_days: number;
}

export interface Application {
  id: string;
  name: string;
  description: string;
  roles: ApplicationRole[];
  max_duration_days: number;
}

export interface PendingApproval {
  approval: Approval;
  request: AccessRequest;
}

export interface AuditEntry {
  id: number;
  timestamp: string;
  correlation_id: string;
  actor_id: string;
  actor_type: string;
  action: string;
  target_type: string;
  target_id: string;
  outcome: string;
  details: Record<string, unknown>;
  hash: string;
}

export interface NotificationItem {
  id: string;
  channel: string;
  subject: string;
  body: string;
  status: string;
  related_id: string | null;
  created_at: string;
}

export interface KnowledgeDocument {
  id: string;
  source: string;
  source_uri: string;
  title: string;
  allowed_groups: string[];
  sensitivity: string;
  injection_flags: number;
  ingested_at: string;
}

export interface MetricsSnapshot {
  counters: Record<string, { labels: Record<string, string>; value: number }[]>;
  histograms: Record<string, { labels: Record<string, string>; count: number; avg: number; p50: number; p95: number }[]>;
}

export interface ApiErrorBody {
  error: { code: string; message: string; correlation_id: string; details: unknown[] };
}
