# Power Automate artifacts

> **Type:** reference definitions (Logic Apps workflow-definition JSON). They document the exact triggers,
> actions, expressions, retry policies and secure-input settings. They are **not** exported solution packages
> (`.zip`) and were not run against a live tenant from this repository. Recreate them in the designer inside the
> `ITSupportAgent` solution, or paste individual actions via *Code view*.

| File | Trigger | Called by | Purpose |
|---|---|---|---|
| `access-request-approval-flow.json` | When an HTTP request is received | Backend `PowerAutomateApprovalProvider` (`APPROVAL_PROVIDER=power_automate`) | Responds 202 immediately, runs **Start and wait for an approval** assigned to the approver resolved by the backend, then POSTs the decision to `/api/v1/approvals/callback` |
| `notify-employee-flow.json` | When an HTTP request is received | Backend `TeamsFlowNotificationProvider` (`NOTIFICATION_PROVIDER=teams_webhook`) | Posts the notification to the employee as the Flow bot in Teams |

## Approval flow – security design

1. **The backend decides who approves.** The flow receives `approverUpn` and only assigns the approval; it never
   looks up managers itself, so approver logic (no self-approval, governance fallback, owner second stage) lives in one
   tested place.
2. **Callback authentication (two factors):**
   * `X-Callback-Token` – a random one-time token generated per approval. The backend stores only its SHA-256 hash
     and invalidates it after use.
   * `ActiveDirectoryOAuth` – the flow obtains an Entra ID token for the API as a dedicated app registration that has
     the `ITSupport.ApprovalCallback` app role and is listed in `ENTRA_ALLOWED_CALLBACK_APP_IDS`.
3. **Responder verification.** The callback sends the responder object id and UPN from the Approvals connector output;
   the backend rejects the decision unless it matches the assigned approver and differs from the requester.
4. **Secure inputs/outputs** are enabled so the callback token and secrets do not appear in run history.
5. The HTTP trigger URL contains a SAS signature – store it only in `POWER_AUTOMATE_APPROVAL_FLOW_URL`
   (Key Vault in production). In tenants that support it, set the trigger to *Specific users in my tenant* /
   OAuth-protected triggers instead of SAS.

## Import steps (summary)

1. In the solution create environment variables `TenantId`, `ApiAudience`, `CallbackClientId` (text) and
   `CallbackClientSecret` (type **Secret**, Azure Key Vault reference).
2. New *Instant cloud flow* → trigger *When an HTTP request is received* → paste the JSON schema from the trigger.
3. Add the actions in order; copy expressions from the JSON (`@triggerBody()?['…']`, `@first(body('…')?['responses'])…`).
4. Save, copy the trigger URL into the backend secret store, set `APPROVAL_PROVIDER=power_automate`.
5. DLP: the flow uses the HTTP connector – put it in the *Business* group only in the environment that hosts this
   solution, or replace the HTTP action with an action of the custom connector (`ApprovalCallback` operation) secured
   by OAuth.
