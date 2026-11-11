# Deployment Guide – Microsoft Tenant Integration

This repository runs end-to-end **locally with mocks**. This guide lists the remaining steps that need a
Microsoft 365 / Entra ID / Power Platform tenant. Nothing below has been executed by this repository; each step
says what is executable code (already in the repo) versus portal configuration (yours to do).

| Area | Executable in repo | Tenant configuration needed |
|---|---|---|
| Backend API | ✅ FastAPI app, Dockerfile | Host it (Azure App Service / Container Apps), HTTPS, Key Vault |
| Entra ID token validation | ✅ `EntraTokenValidator` (unit-tested with locally generated RSA keys) | App registrations, app roles, scopes |
| Manager lookup | ✅ `GraphIdentityProvider` (tested with mocked Graph) | Graph app permission `User.Read.All` |
| Access provisioning | ✅ `EntraGroupProvisioningProvider` (tested with mocked Graph) | Security groups, `GroupMember.ReadWrite.All` + group ownership scoping |
| SharePoint retrieval | ✅ `SharePointGraphSource` (tested with mocked Graph) | `Sites.Selected` grant for one site |
| Copilot Studio agent | ❌ (config) | Create agent, paste instructions, add knowledge + tools |
| Custom connector | ✅ `connector-openapi.yaml` (Swagger 2.0, schema-validated, contract-tested against the API) | Import, configure OAuth |
| Power Automate flows | ⚠️ reference definitions in `power-automate/` | Recreate/import, connections, env vars |
| Application Insights | ✅ OpenTelemetry wiring | Create resource, set connection string, install exporter |

---

## Step 1 – Host the backend

1. Create an Azure Container Apps environment (or App Service for Containers) and an Azure Database for PostgreSQL
   Flexible Server (or keep SQLite only for a single-instance pilot – not recommended).
2. Build and push `infrastructure/docker/backend.Dockerfile` to Azure Container Registry.
3. Enable a **system-assigned managed identity**; grant it `Key Vault Secrets User` on a Key Vault.
4. Store secrets in Key Vault: `graph-client-secret`, `power-automate-approval-flow-url`,
   `power-automate-notify-flow-url`, `applicationinsights-connection-string`, DB password.
5. Environment variables (non-secret): see `.env.example` – set `ENVIRONMENT=prod`, `AUTH_MODE=entra`,
   `KEY_VAULT_URL=https://<vault>.vault.azure.net/`, `PUBLIC_BASE_URL=https://<api-host>`,
   `DATABASE_URL=postgresql+psycopg://...`.
6. Install the optional packages listed at the bottom of `backend/requirements.txt` (`psycopg[binary]`,
   `azure-monitor-opentelemetry-exporter`, `azure-identity`, `azure-keyvault-secrets`).
7. Optional but recommended: front the API with **Azure API Management** (rate-limit-by-key, IP filtering for the
   Power Platform region, request size limits).

## Step 2 – Entra ID app registrations

### 2a. Backend API (`it-support-agent-api`)
* *Expose an API* → Application ID URI `api://it-support-agent` → scope **`access_as_user`** (admins and users).
* *App roles* (allowed member types in brackets):
  `ITSupport.Employee` [Users/Groups], `ITSupport.Manager` [Users/Groups], `ITSupport.Agent` [Users/Groups],
  `ITSupport.Admin` [Users/Groups], `ITSupport.ApprovalCallback` [Applications].
* *Enterprise application → Users and groups*: assign `All Employees` → Employee, `People Managers` → Manager,
  `IT Service Desk` → Agent, `IT Platform Admins` → Admin. Set *Assignment required = Yes*.
* *Token configuration*: add the `groups` claim (Security groups, emit as object IDs) – used for knowledge
  security trimming. For users in > 200 groups, switch to Graph `checkMemberGroups` (overage handling).
* Backend env: `ENTRA_TENANT_ID`, `ENTRA_API_AUDIENCE=api://it-support-agent`.

### 2b. Custom connector client (`it-support-agent-connector`)
* Web platform, redirect URI = the one shown on the custom connector *Security* tab
  (`https://global.consent.azure-apim.net/redirect/...`).
* API permissions → *My APIs* → `it-support-agent-api` → delegated `access_as_user` → grant admin consent.
* Create a client secret (store it in the connector only).

### 2c. Approval callback (`it-support-agent-approval-flow`)
* Client secret → Key Vault → Power Platform environment variable of type *Secret*.
* API permissions → *Application* → `ITSupport.ApprovalCallback` on the backend API → admin consent.
* Add its client ID to `ENTRA_ALLOWED_CALLBACK_APP_IDS`.

### 2d. Graph worker (`it-support-agent-graph`)
* Application permissions: `User.Read.All` (manager lookup), `GroupMember.ReadWrite.All` (provisioning),
  `Sites.Selected` (SharePoint) → admin consent.
* Least privilege: grant `Sites.Selected` *write/read* only to the IT Help site
  (`POST /sites/{site-id}/permissions` with role `read`). Prefer creating the requestable groups as
  **role-assignable groups in a restricted management administrative unit**, or migrate provisioning to
  **Entra ID Governance access packages** (then the adapter calls `accessPackageAssignmentRequests` instead of group
  membership – same interface).
* Backend env: `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, secret in Key Vault, `IDENTITY_PROVIDER=graph`,
  `PROVISIONING_PROVIDER=entra_groups`, `ALLOW_REAL_PROVISIONING=true` (explicit safety switch).
* Replace the placeholder `group_id` values in `backend/app/data/application_catalog.json` with real group object IDs.

## Step 3 – SharePoint knowledge site

1. Create site `IT Help` with library `IT Knowledge Base`; upload the documents from
   `knowledge-base/sample-documents` (front matter can stay; it is ignored by SharePoint).
2. Permissions: library readable by `All Employees`; restricted folder/library for runbooks readable by
   `IT Staff` only (break inheritance).
3. Apply sensitivity labels (Purview): *General* for guides, *Confidential* for runbooks. Labels with encryption
   (*Highly Confidential*) are excluded from ingestion by the adapter.
4. Optional backend RAG over SharePoint: `KNOWLEDGE_SOURCE=sharepoint`, `SHAREPOINT_SITE_ID`, `SHAREPOINT_DRIVE_ID`,
   then call `POST /api/v1/knowledge/ingest` as an admin (schedule it, e.g. hourly).

## Step 4 – Custom connector

1. Power Apps / Copilot Studio → *Custom connectors* → *New* → *Import an OpenAPI file* → `connector-openapi.yaml`.
2. *General*: host = your API host.
3. *Security*: OAuth 2.0 → Identity provider **Microsoft Entra ID** → Client ID/secret of 2b →
   Resource URL `api://it-support-agent` → Scope `api://it-support-agent/access_as_user`. Enable
   *on-behalf-of login* if available in your region.
4. Copy the generated redirect URL back into app registration 2b.
5. *Test* tab: create a connection as a test user and run `ListApplications`, `CreateTicket`.
6. Add the connector to the `ITSupportAgent` solution.

## Step 5 – Power Automate flows

Follow `power-automate/README.md`. Then set backend `APPROVAL_PROVIDER=power_automate` and
`NOTIFICATION_PROVIDER=teams_webhook` and store both trigger URLs in Key Vault.

## Step 6 – Copilot Studio agent

1. Create the agent in the solution environment; configure per `agent-configuration.md`.
2. Paste `agent-instructions.md`.
3. Knowledge → add the SharePoint site.
4. Tools → add the 6 tools from the custom connector using `tool-definitions/*.json` for names, descriptions and
   input settings. Set *Authentication* of each tool connection to **end user credentials**.
5. Customise system topics (Escalate, Fallback, On Error) as described.
6. Settings → Security → *Authenticate with Microsoft*. Settings → Advanced → Application Insights connection string.
7. Test in the test pane with the scenario table in `docs/implementation-guide.md` (activity map shows tool choice).
8. Publish → Channels → Microsoft Teams and Microsoft 365 → share with the pilot security group.

## Step 7 – Governance (see `docs/security.md`)

* Environments Dev → Test → Prod, managed solutions, deployment via Power Platform pipelines.
* DLP policy for the environment: *Business* = SharePoint, Approvals, Teams, Office 365 Users, the custom connector,
  HTTP (only if used by the approval flow); *Blocked* = everything else including consumer connectors.
* Enable Copilot Studio audit logging in Microsoft Purview.

## Step 8 – Verify in the tenant

Run the scenario list in `docs/implementation-guide.md` §6 against the published agent and record your actual
results. The backend's `scripts/smoke_test.py` can be pointed at a *mock-mode* deployment only (it uses dev tokens).
