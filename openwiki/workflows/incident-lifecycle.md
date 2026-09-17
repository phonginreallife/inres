---
type: workflow
title: Incident Lifecycle
description: The three incident states and the guarded transitions between them, what is recorded in the append-only event log, how tenant scoping is enforced at the handler, and why AI analysis and realtime broadcast are queued rather than performed inline.
tags: [incidents, lifecycle, state-machine, audit, events, analytics, realtime]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-afc45e3a3cd7ee37095d0d47
    resource: repo://frontend/inres/src/contexts/NotificationContext.tsx
  - id: openwiki-source-e57746fab1462428c74fc49e
    resource: repo://server/api/authz/middleware.go
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-109f8e2c770360ab09bf8607
    resource: repo://server/api/db/incident_models.go
  - id: openwiki-source-98954dba5890b96e61f3ff01
    resource: repo://server/api/handlers/incident_test.go
  - id: openwiki-source-b500f123f16277acf6022a93
    resource: repo://server/api/handlers/incident.go
  - id: openwiki-source-dbbb30d77aac6ae4971c52ab
    resource: repo://server/api/handlers/webhook.go
  - id: openwiki-source-3959ed3b6b53a23e909f8e0f
    resource: repo://server/api/internal/background/incident.go
  - id: openwiki-source-818fc8b55b30c170fb7e69b9
    resource: repo://server/api/services/incident_analytics_service.go
  - id: openwiki-source-9b6dbc33d6da6ba8d48fc24e
    resource: repo://server/api/services/incident.go
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Incident Lifecycle

An incident is the unit of work in InRes. It is created by an alert, a monitor
or a person; it is acknowledged and resolved; and every step is appended to an
event log.

Related: [Alert ingestion](../workflows/alert-ingestion.md) ·
[Escalation and notifications](../workflows/escalation-and-notifications.md) ·
[Agent extensibility](../ai-agent/extensibility.md)

---

## States

Three, and no others:

```
triggered ──► acknowledged ──► resolved
     └───────────────────────────►┘
```

Alongside `status`, an incident carries `urgency` (`low` / `high`), a severity,
a `P1`–`P5` priority, an `escalation_status` (`none` / `pending` /
`completed`), a `current_escalation_level`, and an `alert_count` that rises with
deduplicated alerts.

### Transitions are guarded in SQL

The guard is in the `WHERE` clause, not in Go:

- **Acknowledge** — `WHERE id = $5 AND status = 'triggered'`. An incident that
  is already acknowledged or resolved cannot be acknowledged again, and two
  concurrent acknowledgements cannot both take effect.
- **Resolve** — `WHERE id = $3 AND status != 'resolved'`. Resolution is
  therefore permitted **from either `triggered` or `acknowledged`** — you need
  not acknowledge before resolving.

Both record who acted (`acknowledged_by`, `resolved_by`) and when.
`resolved_at` is written as `NOW() AT TIME ZONE 'UTC'`, keeping it consistent
with the UTC session timezone both Go entrypoints set.

Note that these updates do not report whether a row matched, so calling
acknowledge on an already-acknowledged incident returns success while changing
nothing — but it does still append an event.

---

## The event log

`incident_events` is append-only and typed:

| Event | Written when |
|---|---|
| `triggered` | Incident created |
| `acknowledged` | Acknowledged |
| `resolved` | Resolved |
| `assigned` | Assignment (see the caveat below) |
| `escalated` | Escalation worker advances a level |
| `note_added` | A note is added |
| `updated` | Fields changed |

Each event carries a JSON `event_data` payload and a `created_by`, and
`GetIncidentEvents` joins the user table so the timeline shows names rather than
UUIDs.

Two design choices are visible in how events are written:

**Names are denormalised into the payload.** `AddNote` resolves the author's
name into `author_name` at write time, and assignment resolves `assigned_to`
alongside `assigned_to_id` with a fallback to the raw id if the lookup fails.
The timeline stays readable even if a user is later renamed or removed.

**Event failures never fail the operation.** Every call site discards the error
with `_ = s.createIncidentEvent(...)`. Losing an audit line is treated as
preferable to failing an acknowledgement — a deliberate trade-off worth knowing
when relying on the log for completeness.

### A gap in assignment auditing

`AssignIncident` updates `assigned_to`, then builds the `eventData` map
including the resolved user name and optional note — and **returns `nil`
without ever calling `createIncidentEvent`**. The payload is constructed and
discarded, so a direct assignment through this method produces no `assigned`
event.

Escalation-driven assignment is unaffected: `IncidentWorker` writes its own
event through its own `createIncidentEvent`.

---

## Creation

`CreateIncident` is the single entry point used by the webhook handler, the
uptime report handler and the API. It persists the incident and then, when the
incident carries an `OrganizationID`, calls
`BroadcastService.BroadcastIncidentAsync(orgID, incident, "INSERT")`.

The organization check is not incidental. Broadcast channels are per
organization (see [web application](../frontend/web-application.md)), so an
incident without one has no channel to be delivered on — the same field that
governs ReBAC visibility governs realtime delivery.

Notification enqueueing, escalation eligibility and analytics all follow from
creation rather than being the caller's responsibility.

---

## Work that is deliberately not inline

Three things happen off the request path.

**Notifications.** Sent from goroutines in `AcknowledgeIncident` and
`ResolveIncident`, so a slow Slack update does not delay the HTTP response. The
notification tells Slack to update its existing message — which is why a web
acknowledgement is reflected in Slack.

**Realtime broadcast.** `BroadcastIncidentAsync` is asynchronous by name; a
Supabase Realtime call never blocks incident creation.

**AI analysis.** `QueueIncidentForAnalysis` publishes to the
`incident_analysis_queue` PGMQ queue; the Python agent consumes it, analyses the
incident with Claude and writes insights back. Three properties matter:

- It is **gated** on `config.App.AIIncidentAnalytics.Enabled`, returning early
  and logging when AI Pilot is off.
- The payload includes `organization_id`, marked `Required for ReBAC tenant
  isolation`, plus optional `project_id` — the consumer must know the tenant to
  read anything else safely.
- Handlers call `QueueIncidentForAnalysisAsync`, which runs in a goroutine and
  **logs rather than returns** errors, so an analysis failure can never block
  incident creation.

An LLM call takes seconds and can fail. Queueing it means the incident exists
and pages someone regardless, and the analysis is an enhancement that arrives
later.

---

## Manual operations

`ManualEscalateIncident` lets a responder escalate immediately rather than
waiting for the timeout, resolving the next level through the same escalation
policy the worker uses.

`GetAssigneeFromEscalationPolicy` resolves who should own an incident, delegating
to `getCurrentOnCallUserFromScheduler` or `getCurrentOnCallUserFromGroup`. This
is the function the webhook handler calls before creating an incident, which is
how an alert arrives already assigned.

`FindIncidentByFingerprint` and `IncrementAlertCount` support deduplication, and
`UpdateIncident` handles general field changes.

---

## Reading incidents

`ListIncidents` takes a filter map built by `authz.GetReBACFilters`, so tenant
scoping is applied uniformly rather than per endpoint (see
[tenancy and authorization](../concepts/tenancy-and-authorization.md)).
`GetIncident` joins assignee, acknowledger, resolver, group, service and
escalation policy so a single read renders a full detail view.

`GetIncidentStats` and `GetIncidentTrends` back the dashboard, the latter taking
an org, project and time range for charting.

`TestIncidentHandler_GetIncident_ReBAC` pins enforcement at the handler
boundary, running the real `IncidentService` against `go-sqlmock` with a mocked
authorizer and asserting both the allowed and denied paths — so authorization
cannot regress to being enforced only in the service layer.
