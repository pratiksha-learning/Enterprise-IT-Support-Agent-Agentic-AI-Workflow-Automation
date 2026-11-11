# Observability

## What is emitted

| Signal | Implementation | Contents |
|---|---|---|
| Structured logs | `observability/logging.py` – one JSON object per line on stdout | timestamp, level, logger, message, `correlation_id`, `trace_id`, `span_id`, context; **all values redacted** |
| Correlation IDs | `RequestContextMiddleware` – accepts a well-formed `X-Correlation-ID` or creates one; returned in the response header, every error body and every audit entry | |
| Traces | OpenTelemetry SDK; FastAPI auto-instrumentation + custom spans `agent.turn`, `agent.tool`, `knowledge.answer`, `knowledge.ingest`, `access_request.submit`, `approval.start`, `access.provision` | exceptions recorded, span status = ERROR |
| Metrics | `MetricsRegistry` mirrors into OTel instruments | see table below |

| Metric (OTel name) | Labels | Meaning |
|---|---|---|
| `api.requests` / `api.latency` (ms) | route, status | API execution latency and volume |
| `agent.executions` / `agent.execution.latency` | intent, outcome | agent turns |
| `agent.tool.invocations` / `agent.tool.latency` | tool, outcome | tool success/failure |
| `approvals.completed` / `approvals.duration` (s) | outcome, channel | approval completion and time-to-decision |
| `downstream.retries` | operation | retry tracking |
| `app.errors` | code | errors by structured error code |
| (registry only) `approvals_blocked`, `provisioning`, `knowledge_queries`, `access_requests` | reason/outcome | security & business KPIs |

Exporters (`observability/telemetry.py`): Azure Monitor if `APPLICATIONINSIGHTS_CONNECTION_STRING` is set and
`azure-monitor-opentelemetry-exporter` is installed; OTLP if `OTEL_EXPORTER_OTLP_ENDPOINT` is set (e.g. the Jaeger
service in `docker-compose.yml` with profile `observability`); console if `OTEL_CONSOLE_EXPORT=true`.

## Local dashboard

`GET /api/v1/admin/metrics` (admin) → rendered by the **Monitoring** tab of the demo UI: KPI tiles, latency p50/p95 per
route and tool, tool invocation table and fault-injection toggles.

## Application Insights – KQL queries

Request latency by route (backend):
```kusto
requests
| where cloud_RoleName == "it-support-agent-api"
| summarize count(), p50 = percentile(duration, 50), p95 = percentile(duration, 95) by name, bin(timestamp, 1h)
| order by timestamp desc
```

Tool invocation success rate:
```kusto
customMetrics
| where name == "agent.tool.invocations"
| extend tool = tostring(customDimensions.tool), outcome = tostring(customDimensions.outcome)
| summarize total = sum(valueSum) by tool, outcome
| evaluate pivot(outcome, sum(total))
| extend success_rate = round(100.0 * success / (success + failure), 1)
```

Approval completion and time-to-decision:
```kusto
customMetrics
| where name == "approvals.duration"
| extend outcome = tostring(customDimensions.outcome)
| summarize decisions = sum(valueCount), avg_hours = sum(valueSum) / sum(valueCount) / 3600 by outcome, bin(timestamp, 1d)
```

Retries and downstream errors:
```kusto
customMetrics
| where name in ("downstream.retries", "app.errors")
| extend label = coalesce(tostring(customDimensions.operation), tostring(customDimensions.code))
| summarize count = sum(valueSum) by name, label, bin(timestamp, 1h)
```

Trace one user journey end to end (correlation id from the UI/error body):
```kusto
union traces, requests, dependencies, exceptions
| where customDimensions.correlation_id == "<correlation-id>" or operation_Id == "<trace-id>"
| project timestamp, itemType, name, message, resultCode, duration
| order by timestamp asc
```

Blocked approval attempts (security signal – alert on > 0):
```kusto
traces
| where message == "audit" and tostring(customDimensions.context) has_any ("self_approval_blocked", "unauthorized_decision_blocked", "callback_rejected")
| summarize attempts = count() by bin(timestamp, 1h)
```

Copilot Studio side (when the agent's App Insights connection is configured):
```kusto
customEvents
| where name in ("BotMessageReceived", "TopicStart", "TopicAction")
| summarize count() by name, tostring(customDimensions.topicName), bin(timestamp, 1d)
```

Suggested alerts: p95 latency of `POST /api/v1/access-requests` > 2 s for 15 min; tool failure rate > 5 %;
`approvals_blocked` > 0; `downstream.retries` spike; approval median time > 24 h.

## Never logged
Passwords, tokens (JWT/Bearer), callback tokens, client secrets, signed URLs, card/ID/phone numbers (pattern
redaction), full emails in audit details (masked). Chat transcripts are stored redacted.
