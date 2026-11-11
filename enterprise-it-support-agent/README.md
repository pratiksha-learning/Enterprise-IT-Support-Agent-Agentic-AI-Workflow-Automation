# Enterprise IT Support Agent

**A Teams / Microsoft Copilot Studio IT helpdesk agent with a Python (FastAPI) backend: grounded knowledge
answers, IT ticket management, and access requests with human manager approval. It is built so that the AI never
holds authority over security decisions.**

The whole workflow runs **locally with no Microsoft 365 tenant**. Identity, approvals, provisioning and notifications
use clearly labelled mock adapters. The real Microsoft adapters (Entra ID, Graph, SharePoint, Power Automate, Teams)
sit behind the same interfaces and are switched on through environment variables.

> **Honest scope:** the local demo is **not** a Copilot Studio or SharePoint deployment. The Copilot Studio agent,
> custom connector and Power Automate flows are delivered as configuration artifacts plus a step-by-step tenant guide.
> The Microsoft adapters are unit-tested against mocked HTTP and have not been run against a live tenant. See
> [what is real vs. mocked](docs/implementation-guide.md#1-what-is-real-vs-mocked).

![Agent chat](docs/screenshots/01-agent-chat.png)

---

## Business problem

Service desks spend much of their time on three repetitive request types:

1. **"How do I…?"** questions that are already answered in IT documentation.
2. **Incidents** that need a well-formed ticket, plus "what's the status of my ticket?" follow-ups.
3. **Access requests**, which need a justification, the right approver, least-privilege roles, time limits and an
   audit trail. Done by hand they are slow and error-prone. Done naively by an AI agent they are dangerous.

This project automates all three in Microsoft Teams. The language understanding lives in the agent. Every rule that
matters for security is enforced deterministically in the backend.

## Features

| Capability | What it does |
|---|---|
| **A. Knowledge Assistant** | Ingestion of `.md/.txt/.pdf/.docx`, heading-aware chunking, embeddings, top-k retrieval, answers with `[n]` citations, a fallback when nothing relevant is found, **security trimming** by group (SharePoint-style permissions), and **neutralising of prompt injection hidden in documents**. A SharePoint (Microsoft Graph) source adapter maps item permissions and sensitivity labels. |
| **B. Ticket Management** | A mock ITSM REST API: create, get, status, list, history, and status updates driven by a state machine with RBAC (agents update, requesters may cancel). Pydantic validation, correct HTTP codes, structured errors, idempotency keys and SQLite/PostgreSQL persistence. Includes a Swagger 2.0 spec for a Power Platform custom connector. |
| **C. Access Requests** | Collects app, role, duration and justification. Identity comes from the token, the manager is resolved through an identity-provider adapter, and approval is started (mock or Power Automate). The backend validates the decision and the approver identity, provisions exactly one role group (simulated in mock mode), stores a time-bound grant, notifies the employee and audits every step. Privileged roles need a **second approval from the application owner**. |
| **Agent orchestration** | A local emulator of Copilot Studio generative orchestration: guardrails, intent routing, slot filling, confirmation, then a tool call. Plus paste-ready Copilot Studio instructions, tool definitions, topics and fallback design. |
| **Security** | Authentication is separate from authorisation (RBAC). No self-approval on any path. The LLM cannot approve (there is no approve tool, and callbacks need a one-time token plus a service principal). Also: idempotency, rate limiting, redaction, a hash-chained audit log, and fail-safe configuration (`ALLOW_REAL_PROVISIONING`). |
| **Observability** | JSON logs, correlation IDs, OpenTelemetry traces and metrics (API latency, tool success/failure, agent turns, approval completion and duration, retries, errors), an exporter for Application Insights or OTLP, a local monitoring dashboard and KQL queries. |
| **Demo UI** | React + TypeScript with a persona switcher. Pages: chat, tickets, access requests, approvals inbox, knowledge sources, audit trail and monitoring with fault injection. |

## Architecture

```mermaid
flowchart LR
  U[Employee in Teams] -->|Entra ID SSO| CS[Copilot Studio agent<br/>generative orchestration]
  CS -->|knowledge, user-trimmed| SP[(SharePoint IT KB)]
  CS -->|custom connector, OAuth as user| API[IT Support API - FastAPI]
  API --> DB[(PostgreSQL / SQLite)]
  API -->|manager lookup| ENTRA[Entra ID via Graph]
  API -->|start approval| PA[Power Automate Approvals]
  PA -->|card| M[Manager in Teams]
  PA -->|one-time token + app token| API
  API -->|add to role group, time-bound| ENTRA
  API -->|notify| T[Teams Flow bot]
  API -. OpenTelemetry .-> AI[Application Insights]
```

```mermaid
sequenceDiagram
  actor E as Employee
  participant A as Agent
  participant API as Backend
  participant IdP as Identity (Graph/mock)
  participant AP as Approvals (Power Automate/mock)
  actor M as Manager
  participant P as Provisioning (Entra/mock)
  E->>A: I need access to Salesforce
  A-->>E: role? duration? justification? confirm?
  A->>API: POST /access-requests (Idempotency-Key)
  API->>IdP: resolve manager (never the requester)
  API->>AP: start approval
  AP-->>M: approval card
  M->>AP: Approve
  AP->>API: callback (token + responder identity)
  API->>API: verify approver == assigned and != requester
  API->>P: grant one role group, time-bound
  API-->>E: notification + audit trail
```

More detail: [architecture](docs/architecture.md) · [sequence diagrams](docs/sequence-diagrams.md) ·
[security](docs/security.md) · [observability](docs/observability.md) · [API](docs/api-documentation.md) ·
[implementation guide](docs/implementation-guide.md) · [tenant deployment](copilot-studio/deployment-guide.md)

## Technology stack

| Layer | Technology |
|---|---|
| Agent (tenant) | Microsoft Copilot Studio (generative orchestration), Microsoft Teams, Microsoft 365 Copilot |
| Identity | Microsoft Entra ID (app roles, OAuth 2.0, JWKS validation), Microsoft Graph |
| Knowledge | SharePoint Online; local RAG (feature-hashing embeddings, extractive answers); optional Azure OpenAI |
| Workflow | Power Automate (Approvals, Teams), Power Platform custom connector (OpenAPI 2.0) |
| Backend | Python 3.10+, FastAPI, Pydantic v2, SQLAlchemy 2.0, SQLite / PostgreSQL, httpx, PyJWT |
| Observability | OpenTelemetry SDK, Azure Monitor exporter / OTLP, structured JSON logging |
| Frontend | React 18, TypeScript, Vite |
| Quality | pytest (unit + integration), live HTTP smoke test, OpenAPI schema validation, GitHub Actions CI |
| Packaging | Docker, docker-compose (SQLite, Postgres and Jaeger profiles) |

## Repository structure

```
enterprise-it-support-agent/
├── backend/
│   ├── app/
│   │   ├── api/            REST controllers + RBAC dependencies
│   │   ├── agents/         intent router, tool registry, local orchestrator
│   │   ├── services/       tickets, access workflow, knowledge (RAG), audit, notifications, idempotency
│   │   ├── models/         SQLAlchemy ORM + Pydantic schemas
│   │   ├── repositories/   data access
│   │   ├── security/       auth (mock + Entra), RBAC, redaction, prompt guard, rate limit, secrets
│   │   ├── integrations/   identity, approvals, provisioning, notifications, sharepoint, llm, graph client
│   │   ├── observability/  logging, OpenTelemetry, metrics, middleware
│   │   └── data/           mock directory + application catalog
│   ├── tests/              unit/ and integration/
│   ├── scripts/smoke_test.py
│   └── requirements*.txt
├── frontend/src/           components/ pages/ services/ types/
├── copilot-studio/         agent-instructions.md, agent-configuration.md, tool-definitions/,
│                           connector-openapi.yaml, power-automate/, deployment-guide.md
├── knowledge-base/sample-documents/
├── infrastructure/         docker/, docker-compose.yml
├── docs/                   architecture, sequence diagrams, security, API, observability, implementation guide, screenshots
├── .github/workflows/ci.yml
├── .env.example  .gitignore  LICENSE  README.md
```

## Quick start

Prerequisites: Python 3.10+ and Node.js 18+.

```bash
git clone https://github.com/pratiksha-learning/Enterprise-IT-Support-Agent-Agentic-AI-Workflow-Automation.git enterprise-it-support-agent && cd enterprise-it-support-agent

# 1) Backend  ->  http://localhost:8000/docs
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000

# 2) Frontend (new terminal)  ->  http://localhost:5173
cd frontend
npm install
npm run dev
```

Pick a persona in the top-right corner. All personas are fictional:

| Persona | Roles | Use it to show |
|---|---|---|
| Alice Employee | employee (Finance) | chat, tickets, access requests |
| Bob Manager | manager (Alice's manager) | approval inbox |
| Carol IT Agent | it_agent | all tickets, IT-only knowledge, audit |
| Dave Admin | admin, owner of Salesforce/GitHub/AWS | monitoring, fault injection, retry provisioning, second-stage approvals |
| Erin Director | manager, has no manager herself | governance fallback |
| Frank Misconfigured | directory says he is his own manager | self-approval prevention |
| Grace Governance | governance approver | fallback approvals |

Docker (not built in the environment this repo was produced in, because no Docker daemon was available there):
`docker compose -f infrastructure/docker-compose.yml up --build` starts the API on :8000 and the UI on :8080.

## Screenshots and demo

These screenshots were captured from the local app running in mock mode.

| | |
|---|---|
| ![Guardrails and fallback](docs/screenshots/02-guardrails-and-fallback.png) Guardrails and RAG fallback | ![Approval inbox](docs/screenshots/03-manager-approval-inbox.png) Manager approval inbox |
| ![Access audit](docs/screenshots/04-access-request-audit-and-notification.png) Provisioned request, audit timeline, notification | ![Audit trail](docs/screenshots/05-audit-trail.png) Hash-chained audit trail |
| ![Monitoring](docs/screenshots/06-monitoring-dashboard.png) Monitoring and fault injection | ![Knowledge](docs/screenshots/07-knowledge-security-trimming.png) Security-trimmed knowledge sources (IT agent view) |

A step-by-step 8-minute demo script is in [docs/implementation-guide.md](docs/implementation-guide.md#4-demo-script--8-minutes).

## API examples

```bash
BASE=http://localhost:8000
ALICE=$(curl -s -X POST $BASE/api/v1/auth/dev-token -H 'Content-Type: application/json' \
        -d '{"user_id":"u-alice"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST $BASE/api/v1/knowledge/query -H "Authorization: Bearer $ALICE" \
     -H 'Content-Type: application/json' -d '{"question":"How do I reset my password?"}'

curl -s -X POST $BASE/api/v1/access-requests -H "Authorization: Bearer $ALICE" \
     -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-0001' \
     -d '{"application_id":"github-enterprise","role":"reader","duration_days":30,
          "justification":"Read platform repositories during onboarding"}'
```

Example error (sending the same request again with a new key):

```json
{"error":{"code":"DUPLICATE_REQUEST","message":"You already have an active request or grant for this application role",
          "correlation_id":"…","details":[{"request_number":"ACC100001","status":"pending_approval"}]}}
```

Full endpoint list and a curl walkthrough: [docs/api-documentation.md](docs/api-documentation.md).

## Security design (summary)

* **The LLM proposes and the backend decides.** The requester comes from the token and approvers are resolved
  server-side. Request schemas reject unknown fields such as `requester_id` or `status`.
* **No self-approval on any path:** approver routing, direct decisions and Power Automate callbacks are all covered,
  even if the data is tampered with.
* **The agent has no approve tool.** Callbacks need a single-use token (stored hashed) and, in Entra mode, an
  allow-listed service principal with the `ITSupport.ApprovalCallback` app role.
* **Least privilege:** each role maps to one group, grants are time-bound and revoked by an expiry sweep, and the
  real adapter refuses any group outside the catalog. Real grants also need `ALLOW_REAL_PROVISIONING=true`.
* **RAG safety:** documents are trimmed by permission before scoring, instruction-like text in documents is removed,
  and anything with an unknown ACL or a Highly Confidential label is excluded.
* **Other controls:** idempotency keys, rate limiting, redaction in logs, audit and transcripts, and a tamper-evident
  audit chain.

Tenant guidance for Entra ID, SharePoint permissions, Power Platform DLP and sensitivity labels is in
[docs/security.md](docs/security.md).

## Testing

```bash
cd backend
pytest                                        # unit + integration tests
pytest --cov=app --cov-report=term            # with coverage
python scripts/smoke_test.py --base-url http://localhost:8000   # live HTTP checks against a running server

cd ../frontend && npm run build               # TypeScript type-check + production build
```

The suite covers every endpoint, approval and rejection, two-stage approval, three self-approval paths, duplicate and
idempotent requests, unauthorised access (401/403/404), outages in identity, approval, provisioning and
notification, Graph and Power Automate adapter errors, RAG fallback, security trimming, document injection, ticket
lifecycle, rate limiting, redaction, audit tamper detection and a connector-spec-to-API contract test. The mapping
from scenario to test is in [docs/implementation-guide.md](docs/implementation-guide.md#6-test-scenarios-automated).

## Limitations

* The local embeddings are **lexical** (feature hashing with stemming and synonyms), not neural. Answers are
  extractive. Switch to Azure OpenAI for semantic retrieval and generated answers.
* The local orchestrator uses rules, not an LLM. In the tenant, Copilot Studio's generative orchestration does
  the routing.
* The Microsoft adapters are tested against mocked HTTP only. Power Automate flows are reference definitions, not
  exported solution packages.
* The rate limiter and fault injector live in memory for one process. The schema is created at start-up, with no
  Alembic migrations yet.
* The PostgreSQL support and Docker images are configured but were not exercised in the environment this repo was
  built in.

## Future enhancements

* Entra ID Governance **access packages** and access reviews instead of direct group membership.
* A ServiceNow or Jira Service Management adapter behind the ticket service.
* Adaptive Card approvals with Teams SSO in the custom Teams app. Escalation to the manager's manager on timeout.
* Hybrid retrieval (BM25 plus vectors) with Azure AI Search, and an evaluation set for answer quality.
* Alembic migrations, a Redis-backed rate limiter and idempotency store, and Bicep/Terraform for Azure resources.
* Exporting the audit log to Microsoft Sentinel, with analytics rules for blocked approval attempts.

## License

[MIT](LICENSE). All people, companies and identifiers in the sample data are fictional.
