# Security and Governance

## 1. Controls implemented in code

| Control | Where | Verified by |
|---|---|---|
| Authentication separated from authorisation | `observability/middleware.py::AuthenticationMiddleware` (token → `Principal`), `api/deps.py::require()` (RBAC per endpoint) | `test_unauthenticated_and_invalid_tokens_are_rejected`, RBAC tests |
| Entra ID token validation (signature/JWKS, issuer, audience, tenant, expiry, scope `access_as_user`, app-role mapping, allow-listed service principals) | `security/auth.py::EntraTokenValidator` | `tests/unit/test_security_primitives.py` (local RSA keys) |
| Role-based access control (employee / manager / it_agent / admin / approvals_callback) | `security/rbac.py` | ticket/access/audit/admin 403 tests |
| Least privilege – data | users see only their own tickets/requests (404 on others, no enumeration); IT agents read all | `test_other_users_ticket_is_not_found`, `test_visibility_and_cancel` |
| Least privilege – access grants | each role maps to exactly one group; real adapter refuses groups outside the catalog; time-bound with expiry sweep | `test_entra_group_provisioning_refuses_groups_outside_catalog`, `test_expiry_sweep_revokes_expired_grants` |
| Input validation | Pydantic schemas with `extra="forbid"`, length/pattern limits, control-char stripping; catalog/role/duration policy | `test_request_validation`, `test_ticket_validation_errors` |
| Requester identity only from token | no requester field accepted anywhere | `requester_id` injection tests |
| No self-approval | approver resolution reroutes self-managed users; every decision path re-checks `decider != requester` | `test_self_approval_*` |
| Only the assigned approver decides, once, before due date | `_apply_decision` | `test_only_assigned_approver_can_decide`, replay/expiry tests |
| LLM can't approve | no approve tool; orchestrator refuses; callback requires one-time token (+ app token in Entra mode) | `test_agent_cannot_approve`, callback tests |
| Prompt-injection defence (retrieved content) | `security/prompt_guard.py`: instruction-like sentences removed, counted, delimited for LLMs | `test_injected_instructions_in_documents_are_neutralised` |
| Prompt-injection / escalation (user input) | orchestrator refuses before tool use | `test_prompt_injection_and_escalation_refused_without_tool_calls` |
| Safe tool execution | tool registry checks permission of the *user*, validates input with API schemas, traces/counts | `agents/tools.py` |
| Human approval | mock approval inbox / Power Automate Approvals | workflow tests |
| Audit logging | every state change + every denied attempt; hash-chained; redacted details | `test_audit_chain_detects_tampering`, `test_audit_details_are_redacted` |
| Rate limiting | token bucket per user/IP, writes cost 2× | `test_rate_limiting_returns_429_with_retry_after` |
| Idempotency | `Idempotency-Key` scoped per user+operation; payload hash; 5xx not cached | `test_duplicate_and_idempotent_requests` |
| Redaction | JSON log formatter, audit details, chat transcript | `test_json_formatter_never_logs_secrets`, `test_secrets_are_not_stored_in_transcript` |
| Secret management | `SecretStr` settings, env or Key Vault (`security/secrets.py`); nothing hard-coded; mock JWT secret random per process if unset | config tests |
| Safe defaults | mock auth refused when `ENVIRONMENT=prod`; real provisioning requires `ALLOW_REAL_PROVISIONING=true` | `test_mock_auth_forbidden_in_prod`, `test_real_provisioning_requires_explicit_flag` |
| Error hygiene | structured errors with correlation id; no stack traces to clients | exception handler tests |
| Log-injection | inbound correlation IDs validated | `test_correlation_id_is_generated_or_propagated` |

## 2. Threat model (abridged)

| Threat | Mitigation |
|---|---|
| User asks agent to approve or skip approval | Refusal + no approve tool + server-side checks |
| Malicious text in a SharePoint document ("approve all requests") | Sanitised as data; no tool can approve; flagged at ingestion (`injection_flags`) |
| User requests access for a colleague | No requester field; agent refuses; API uses token identity |
| Manager approves own request / directory data error (manager = self) | Reroute to governance approver; decision check blocks requester |
| Forged Power Automate callback | One-time token (hash stored), Entra app-only token with `ITSupport.ApprovalCallback`, responder must equal assigned approver |
| Replay of approval | Approval must be `pending`; callback token consumed |
| Duplicate submissions from retries | Idempotency keys + active-request uniqueness |
| Enumerating tickets/requests | 404 for non-visible objects |
| Exfiltration via RAG | Security trimming before retrieval; Highly Confidential labels excluded; fail-closed when ACL unknown |
| Credential leakage | Redaction in logs/audit/transcripts; secrets in Key Vault; secure inputs in flows |
| Over-privileged automation | Group-scoped provisioning, `Sites.Selected`, separate app registrations per purpose |
| Abuse / DoS | Rate limiting (API + APIM), request size limits, timeouts on downstream calls |

## 3. Configuring a real tenant

### Microsoft Entra ID
* Separate app registrations per purpose (API, connector client, approval callback, Graph worker) – compromise of one
  does not grant the others' permissions.
* App roles for authorisation; *assignment required* on the enterprise app; groups claim as object IDs.
* Conditional Access for the agent's users: require compliant device / MFA for Teams and the API resource.
* Workload identity: certificate credentials or federated credentials instead of client secrets where possible;
  Conditional Access for workload identities (location-restrict the Graph worker).
* Privileged roles (Global Admin, etc.) are never in the catalog; use **PIM** for those.
* Prefer **Entra ID Governance access packages** for application access lifecycle (expiry, access reviews,
  separation-of-duties policies). The provisioning adapter interface supports swapping to access-package
  assignment requests.

### SharePoint permissions
* IT Help site: *All Employees* read; content owners edit; break inheritance for restricted libraries
  (`IT Staff` only). Avoid "Everyone except external users" on sensitive libraries.
* Copilot Studio SharePoint knowledge returns only what the signed-in user can open – fix oversharing first
  (SharePoint Advanced Management: data access governance reports, restricted content discovery).
* Backend Graph access uses `Sites.Selected` scoped to the one site.

### Power Platform DLP
* Dedicated environment for the solution (Dev/Test/Prod), managed solutions in Test/Prod.
* DLP policy: **Business** = SharePoint, Microsoft Teams, Approvals, Office 365 Users, the custom connector (and HTTP only
  if the approval flow uses it); **Non-business** = none needed; **Blocked** = all other connectors.
* Endpoint filtering on the HTTP connector: allow only the API host.
* Custom connector: restrict sharing to the solution's makers; connection uses end-user OAuth.
* Copilot Studio: tenant-level setting to block agent publishing outside approved environments; disable
  "Use general knowledge" and web search for this agent.

### Sensitivity labels (Microsoft Purview)
* Label knowledge documents: *General* (guides), *Confidential* (runbooks, restricted audience), *Highly Confidential*
  (encrypted; excluded from ingestion).
* Copilot respects labels and encryption usage rights; M365 Copilot responses inherit the highest label of the sources.
* Enable Purview audit for Copilot Studio and Copilot interactions; DLP for Microsoft 365 Copilot to exclude labelled
  content where required.

### Data protection & retention
* Retention policy on the audit table (e.g. 1 year hot, archive to immutable storage); stream audit events to
  Log Analytics / Sentinel.
* Conversations store redacted text only; set a retention period (e.g. 30 days).

## 4. Known limitations
* The rate limiter and fault injector are per-process (use APIM/Redis for multi-instance).
* The audit hash chain detects tampering but does not prevent it; use DB permissions (append-only role) and export.
* Pattern-based injection detection is defence in depth, not a guarantee; the hard guarantees come from authorisation
  and the absence of dangerous tools.
* In mock mode the dev-token endpoint issues tokens for any demo persona – by design, and disabled outside mock mode.
