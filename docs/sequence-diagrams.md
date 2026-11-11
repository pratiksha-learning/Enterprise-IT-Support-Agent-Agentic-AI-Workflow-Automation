# Sequence Diagrams

## 1. Knowledge question (RAG with security trimming)

```mermaid
sequenceDiagram
  actor E as Employee
  participant A as Agent (Copilot Studio / local orchestrator)
  participant K as KnowledgeService
  participant DB as Chunk store
  E->>A: "How do I set up VPN on my Mac?"
  A->>A: guardrails (secrets, injection, escalation)
  A->>K: search_it_knowledge(question) as user
  K->>DB: load documents where allowed_groups ∩ user.groups ≠ ∅
  K->>K: embed question, cosine top-k, min score
  K->>K: strip instruction-like sentences from chunks
  alt relevant passages found
    K-->>A: answer + citations [n]
    A-->>E: grounded answer with sources
  else nothing relevant
    K-->>A: fallback=true
    A-->>E: "I couldn't find this… shall I create a ticket?"
  end
```

## 2. Ticket creation and status

```mermaid
sequenceDiagram
  actor E as Employee
  participant A as Agent
  participant API as Tickets API
  participant DB as Database
  E->>A: "My laptop screen is flickering"
  A-->>E: summary (title, category, priority) – confirm?
  E->>A: yes
  A->>API: POST /api/v1/tickets (Idempotency-Key)
  API->>API: validate schema (extra fields forbidden)
  API->>DB: insert ticket + event + audit
  API-->>A: 201 INC1001001
  A-->>E: "Created INC1001001 (P3)"
  E->>A: "status of INC1001001"
  A->>API: GET /api/v1/tickets/INC1001001/status
  API-->>A: 200 {status} (404 if not the user's ticket)
```

## 3. Access request – approved

```mermaid
sequenceDiagram
  actor E as Employee
  participant A as Agent
  participant API as Access API
  participant IdP as Identity provider (Graph / mock)
  participant PA as Approval service (Power Automate / mock)
  actor M as Manager
  participant PROV as Provisioning (Entra groups / mock)
  participant N as Notifications
  E->>A: "I need access to Salesforce"
  A->>API: GET /access/applications
  A-->>E: ask role, duration, justification → confirm
  E->>A: yes
  A->>API: POST /access-requests (Idempotency-Key, no requester field)
  API->>API: identity from token, validate catalog/role/duration, duplicate check
  API->>IdP: get_user(requester), get_manager(requester)
  API->>API: approver must differ from requester (else governance fallback), privileged adds app owner stage
  API->>API: persist request + approval(stage 1) + hashed callback token + audit
  API->>PA: start(approval task)
  PA-->>M: Approvals card in Teams
  API-->>A: 201 ACC100001 pending_approval
  A-->>E: "Submitted, waiting for Bob"
  M->>PA: Approve
  PA->>API: POST /approvals/callback (X-Callback-Token + Entra app token, responder id/upn)
  API->>API: verify token hash, service principal role, responder == assigned approver ≠ requester, not expired, still pending
  API->>PROV: grant(user, role group) with retry
  PROV-->>API: ok
  API->>API: status provisioned, grant row (expires_at), audit
  API->>N: notify employee
  N-->>E: "Access granted until …"
```

## 4. Access request – rejected

```mermaid
sequenceDiagram
  actor M as Manager
  participant API as Access API
  participant N as Notifications
  actor E as Employee
  M->>API: decision = reject (comment)
  API->>API: same validation as approve
  API->>API: approval rejected, request rejected, audit
  API->>N: notify employee with comment
  N-->>E: "ACC100002 rejected: …"
```

## 5. Blocked decisions (self-approval / wrong approver / LLM)

```mermaid
sequenceDiagram
  actor X as Requester or other user
  participant A as Agent
  participant API as Access API
  X->>A: "approve my request ACC100001"
  A-->>X: refused (no approve tool exists)
  X->>API: POST /approvals/{id}/decision
  alt caller == requester
    API-->>X: 403 SELF_APPROVAL_FORBIDDEN (audited)
  else caller ≠ assigned approver
    API-->>X: 403 NOT_ASSIGNED_APPROVER (audited)
  else already decided / expired
    API-->>X: 409 APPROVAL_ALREADY_DECIDED / APPROVAL_EXPIRED
  end
```

## 6. Downstream failure handling

```mermaid
sequenceDiagram
  participant API as Access API
  participant PA as Approval service
  participant PROV as Provisioning
  participant T as Ticket service
  API->>PA: start approval
  PA--xAPI: 503 (retry ×3, exponential backoff)
  API->>API: request = failed, audit, commit
  API-->>API: 503 APPROVAL_SERVICE_UNAVAILABLE (not cached → client may retry same key)
  Note over API,PROV: later, on final approval
  API->>PROV: grant
  PROV--xAPI: error after retries
  API->>API: status provisioning_failed, audit
  API->>T: auto-create P3 access ticket for IT
  API->>API: notify employee "approved, provisioning delayed"
  Note over API: admin POST /retry-provisioning → provisioned
```
