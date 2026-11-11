# Copilot Studio Agent Configuration

> **Type:** configuration guide. Everything here is done in the Copilot Studio / Power Platform
> portal of a real tenant. Nothing in this file has been deployed by this repository.

## 1. Agent settings

| Setting | Value | Why |
|---|---|---|
| Name | IT Helpdesk Agent | |
| Solution | `ITSupportAgent` (unmanaged in Dev, managed in Test/Prod) | ALM via pipelines |
| Orchestration | **Generative orchestration: ON** | LLM planner chooses knowledge / tools / topics from their descriptions |
| Instructions | `agent-instructions.md` | |
| Model knowledge ("Use general knowledge") | **OFF** | policy answers must come from company sources only |
| Web search | OFF | |
| Content moderation | High | |
| Authentication | **Authenticate with Microsoft** (Teams & M365 Copilot SSO); *Require users to sign in* | user identity flows to tools |
| Channels | Microsoft Teams + Microsoft 365 Copilot | |
| Availability | Pilot security group first (e.g. `SG-ITAgent-Pilot`) | staged rollout |

## 2. Knowledge

| Source | Configuration |
|---|---|
| SharePoint site `https://contoso.sharepoint.com/sites/ITHelp` (document library *IT Knowledge Base*) | Add as knowledge source. Description: *"IT how-to guides and IT policies: VPN, passwords, MFA, laptops, Outlook/Teams, printers, access requests."* Copilot Studio retrieves with the **signed-in user's permissions**, so security trimming is native. |
| Optional: restricted IT runbooks library | Separate source, library permissions = `IT Staff` group only. |

Local equivalent: `knowledge-base/sample-documents` (front-matter `allowed_groups` mirrors SharePoint permissions).

## 3. Tools (actions)

Import `connector-openapi.yaml` as a **custom connector** (see `deployment-guide.md`), then in the agent
*Tools → Add a tool → Custom connector*. Tool names/descriptions/input descriptions are in
`tool-definitions/*.json` – copy them exactly, because generative orchestration selects tools using these texts.

| Tool | Connector operation | Confirmation |
|---|---|---|
| CreateTicket | `CreateTicket` | Ask before running (also in instructions) |
| GetTicketStatus | `GetTicketStatus` | No |
| ListMyTickets | `ListMyTickets` | No |
| ListRequestableApplications | `ListApplications` | No |
| SubmitAccessRequest | `SubmitAccessRequest` | Ask before running |
| GetAccessRequestStatus | `GetAccessRequest` / `ListMyAccessRequests` | No |

Input settings in each tool:

* `Idempotency-Key` (SubmitAccessRequest / CreateTicket): **Set as a value** (not asked from user) =
  `Concatenate(System.Conversation.Id, "-", Text(Now(), "yyyyMMddHHmm"))`. A retried call inside the same minute
  returns the original result instead of creating a duplicate.
* No tool has a `requester` input – identity comes from the user's OAuth token on the connector connection
  (*end-user authentication*), so the agent cannot act as anyone else.
* There is deliberately **no approve/reject tool**. Approval is human-only (Power Automate Approvals).

## 4. Topics

| Topic | Type | Behaviour |
|---|---|---|
| Conversation Start | System (customise) | Greeting + 3 example prompts |
| Escalate | System (customise) | Offer IT Service Desk channel/phone, optionally call CreateTicket with P2 |
| Fallback | System (customise) | After 2 unrecognised turns: show menu "Ask a question / Report an issue / Request access / Talk to a person" |
| On Error | System (customise) | Friendly message with `System.Conversation.Id` for support; log to Application Insights (custom event `AgentError`) |
| Security incident | Custom, trigger phrases: *phishing, lost laptop, stolen phone, hacked* | Immediate guidance + create P2 ticket (no knowledge lookup delay) |
| Request access (optional) | Custom | Deterministic version of the access flow: Question nodes for app (choice list from ListRequestableApplications), role, duration, justification → adaptive card confirmation → SubmitAccessRequest. Use when you want zero LLM involvement in slot filling. |

All other routing is handled by generative orchestration.

## 5. Input / output schemas

The authoritative JSON schemas are in `tool-definitions/` and in the OpenAPI spec. Summary:

* **CreateTicket** in: `title` (5–120), `description` (10–4000), `category` enum, `priority` P1–P4 → out: `ticket_number`, `status`, `priority`, `category`, `created_at`.
* **SubmitAccessRequest** in: `application_id`, `role`, `duration_days` (1–365, policy max applies), `justification` (20–1000) → out: `request_number`, `status`, `required_stages`, `approvals[0].approver_upn`.
* Errors: `{ "error": { "code", "message", "correlation_id", "details" } }` with HTTP 401/403/404/409/422/429/503.

## 6. Analytics & telemetry

* Copilot Studio → *Settings → Advanced → Application Insights*: paste the **connection string** of the same
  Application Insights resource used by the backend; enable *Log activities* and *Log sensitive activity properties = OFF*.
* KQL queries for both sides are in `docs/observability.md`.
