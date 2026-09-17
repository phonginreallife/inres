---
type: workflow
title: Alert Ingestion and Routing
description: The path an external monitoring alert takes through InRes - webhook validation, per-provider normalization to a common severity and status vocabulary, fingerprint deduplication, service and assignee resolution, and the fallback chain that matches a resolve event to its incident.
tags: [webhooks, alerts, routing, deduplication, prometheus, datadog, incidents]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-c001bc124cb75a5106f3005e
    resource: repo://server/api/handlers/webhook_types.go
  - id: openwiki-source-dbbb30d77aac6ae4971c52ab
    resource: repo://server/api/handlers/webhook.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-ed91ce2df4687ec1f3a7ce3c
    resource: repo://server/api/services/routing.go
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Alert Ingestion and Routing

Every external alert enters through one endpoint:

```
POST /webhook/:type/:integration_id
```

This route is **public** - no JWT, no API key. The integration id in the path is
the credential, which is why it must be treated as a secret. Validation happens
against the database rather than against a token.

Related: [Incident lifecycle](../workflows/incident-lifecycle.md) ·
[Escalation and notifications](../workflows/escalation-and-notifications.md) ·
[Authentication and identity](../concepts/authentication-and-identity.md)

---

## Stage 1 - Validation

`ReceiveWebhook` applies three checks before parsing anything:

1. The integration **exists** → 404.
2. The integration **is active** → 403.
3. The integration's stored `type` **matches the URL's type segment** → 400.

That third check is the interesting one: it prevents a Datadog payload being
posted to a Prometheus integration's URL and silently misparsed.

Only then is the body bound as a generic `map[string]interface{}`. The handler
also updates the integration's heartbeat, and a heartbeat failure is logged but
does **not** fail the webhook - health bookkeeping must never drop an alert.

---

## Stage 2 - Provider normalization

Seven provider types are dispatched by `integrationType`, with anything
unrecognised falling through to the generic processor:

`prometheus`, `datadog`, `grafana`, `pagerduty`, `coralogix`, `webhook`, `aws`.

Each produces a slice of `ProcessedAlert` - the internal shape carrying alert
name, description, summary, severity, priority, status, labels and fingerprint.
Everything downstream works only on that shape, so adding a provider means
adding one processor and one `case`.

Each provider also retains a `...Legacy` variant of its processor alongside the
current one.

### The common vocabulary

Providers describe the same two things - *how bad* and *is it still happening* -
in mutually incompatible terms. Normalization collapses them to a shared
severity scale and a two-valued status.

**Status** becomes `firing` or `resolved`:

| Provider | Resolved when |
|---|---|
| Prometheus | `status: "resolved"` |
| Datadog | transition `recovered` / `ok` / `info` |
| Grafana | state `ok` |
| AWS | state `OK` |

Every mapper **defaults to `firing`** on an unrecognised value. That asymmetry
is deliberate: a misparsed alert that pages someone is recoverable, while one
silently treated as resolved is not.

**Severity** normalizes to `critical` / `high` / `warning` / `low` / `info`:

- Grafana: `alerting` → critical, `pending` → warning, `ok` → info.
- AWS: `ALARM` → critical, `INSUFFICIENT_DATA` → warning, `OK` → info.
- Datadog: `P1`-`P5` → critical, high, warning, low, info.
- Unrecognised values default to `warning`.

`mapSeverityToPriority` runs the inverse mapping, producing PagerDuty/Datadog
style `P1`-`P5` from a severity, defaulting to `P3`. Keeping both directions is
what lets InRes accept and emit either vocabulary.

Datadog timestamps need their own handling - `parseDatadogTimestamp` deals with
Unix milliseconds delivered as a *string*.

These mappings are pinned by tests (`webhook_prometheus_test.go`,
`webhook_datadog_test.go`, `webhook_pagerduty_test.go`,
`webhook_coralogix_test.go`), which matters because each payload shape is owned
by an external vendor.

---

## Stage 3 - Routing by status

`routeAlert` branches on the normalized status: `firing` creates, `resolved`
resolves, and an unknown status logs a warning and is treated as firing.

Each alert is routed independently inside a loop, and **a failure on one alert
is logged and the loop continues** - one malformed alert in a batch does not
discard the rest. The endpoint returns 200 with an alert count regardless.

---

## Stage 4 - Deduplication

Before creating anything, a firing alert with a fingerprint is checked against
existing incidents. On a hit, the handler **increments the existing incident's
alert count and returns** rather than creating a duplicate.

This is what makes a flapping alert produce one incident with a rising count
instead of dozens of incidents. The fingerprint is also written into
`incident.Labels["fingerprint"]`, which is what makes the later lookup possible.

Prometheus supplies a fingerprint natively; the uptime Worker sets
`fingerprint: monitor.id` for the same reason (see
[uptime monitoring](../monitoring/uptime-monitoring.md)).

---

## Stage 5 - Service and assignee resolution

Resolution happens **before** incident creation, so the incident is written once
with everything already attached:

1. Fetch the services connected to this integration. None → no service, and the
   incident is still created.
2. For each, evaluate `matchesRoutingConditions` against the alert. Empty
   conditions match everything.
3. On the **first** match, load the service and stop - first-match-wins, so
   service-integration ordering is significant.
4. If the service carries both an escalation policy and a group, resolve the
   on-call assignee via `GetAssigneeFromEscalationPolicy`, recording the method
   as `escalation_policy`.

A resolution failure is logged and creation continues with whatever was
resolved. An unroutable alert still becomes a visible incident rather than
disappearing.

---

## Stage 6 - Atomic creation

`createIncidentAtomic` assembles the incident in one write:

- Title from the alert name, or from the summary when it differs from the
  description.
- Status `triggered`, source `webhook`, urgency **high by default**, dropped to
  **low** for `info` and `warning` severities.
- Labels from the alert, plus the fingerprint.
- **`OrganizationID` from the integration** - the comment marks this `CRITICAL
  for ReBAC visibility`. Without it the incident exists but no user can see it
  through the tenant-scoped queries described in
  [tenancy and authorization](../concepts/tenancy-and-authorization.md).
- `ProjectID` when the integration has one.
- `ServiceID`, `EscalationPolicyID` and `GroupID` from the resolved service.
- `AssignedTo` and `AssignedAt` (UTC) when an assignee was resolved.

Creating the incident through `IncidentService` is what triggers notification
enqueueing and escalation.

---

## The resolve path

A `resolved` alert must find the incident it belongs to. `findIncidentByAlert`
tries three strategies in descending reliability:

1. **Fingerprint** - exact, the intended path.
2. **Labels** - `alertname` + `instance` + `job`, attempted only when both
   alertname and instance are present.
3. **Title match** - last resort, matching on alert name alone.

Strategy 3 is explicitly labelled a last resort, and its weakness is worth
knowing: two distinct incidents sharing an alert name can resolve the wrong one.
Configuring providers to send fingerprints avoids ever reaching it.

If nothing matches, the handler logs a warning and returns **without error** -
a resolve for an incident that was already closed manually is a normal event,
not a failure.

When an incident is found, it is resolved through `IncidentService` (so
notifications fire) and attributed to a **system user chosen by integration
type** via `GetSystemUserBySource`. The audit trail therefore records which
provider auto-resolved it rather than attributing it to a human.

---

## Routing tables

`services/routing.go` provides a richer, separately configurable layer:
`RoutingTable`s containing ordered `RoutingRule`s, each with match conditions
and optional time conditions.

`RouteAlert` evaluates active tables and returns a `RoutingResult`;
`TestRouting` runs the same evaluation against hypothetical attributes for
dry-run testing. Conditions support nested `and`/`or`, severity and source
matching, and custom attribute matching. Every evaluation is recorded by
`logRouteMatch` with the table, rule and evaluation time in milliseconds, and
`GetRoutingHistory` exposes that per alert - so "why did this alert go there?"
is answerable after the fact.
