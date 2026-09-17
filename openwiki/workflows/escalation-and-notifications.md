---
type: workflow
title: Escalation and Notification Delivery
description: How an unacknowledged incident climbs its escalation policy - the polling worker, its SQL-side timeout evaluation and concurrency-safe claim, the five target types - and how notifications fan out to Slack, push and in-app realtime through PGMQ.
tags: [escalation, notifications, pgmq, slack, fcm, workers, oncall]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-0f4ac35ea5330167c567f09e
    resource: repo://deploy/helm/inres/values.yaml
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-0d58800e4a6061a65eeff1bb
    resource: repo://server/api/cmd/worker/main.go
  - id: openwiki-source-3959ed3b6b53a23e909f8e0f
    resource: repo://server/api/internal/background/incident.go
  - id: openwiki-source-3b6b6b719c0d61055a052fb5
    resource: repo://server/api/internal/background/notification.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-ab7481af14611f19af59d8c8
    resource: repo://server/api/services/escalation.go
  - id: openwiki-source-ccbf969471dc9c71ba071f2f
    resource: repo://server/api/services/fcm_service.go
  - id: openwiki-source-9b6dbc33d6da6ba8d48fc24e
    resource: repo://server/api/services/incident.go
  - id: openwiki-source-6f0414a635b71fb9cbb97ef6
    resource: repo://server/api/services/realtime_broadcast.go
  - id: openwiki-source-7fa4739d35e8934014bbb4c3
    resource: repo://server/slack-worker/slack_repository.py
  - id: openwiki-source-95cb700da6b5ccaddcf27b7a
    resource: repo://server/slack-worker/slack_worker.py
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Escalation and Notification Delivery

Escalation answers one question repeatedly: *has this incident been sitting in
`triggered` longer than its current level allows?* Delivery answers a different
one: *how does the assigned person find out?*

The two are separated by a queue, which is what lets a Slack outage delay
notification without stalling escalation.

Related: [Incident lifecycle](../workflows/incident-lifecycle.md) ·
[On-call scheduling](../workflows/oncall-scheduling.md) ·
[Architecture overview](../architecture/overview.md)

---

## Where escalation actually runs

Two implementations exist and it matters which is live.

`services/escalation.go` owns escalation **policy CRUD** - creating, updating
and reading policies, levels and their target metadata - and this part is fully
used. It also contains an alert-driven `ProcessAlert` chain, but that path's
`notifyCurrentSchedule`, `notifyUser`, `notifyGroup`, `notifyExternal` and
`scheduleNextEscalationStep` methods are **`TODO` stubs that log and return
`nil`**. Only `notifyScheduler` is implemented.

The escalation that actually pages people is
`internal/background/incident.go` - `IncidentWorker`, started as a goroutine by
both `cmd/server` and `cmd/worker`.

---

## The escalation loop

`StartIncidentWorker` ticks every **5 seconds** (the comment beside it still
says 30) and calls `processEscalations`.

### Timeout evaluation happens in SQL

`getIncidentsNeedingEscalation` does the whole decision in one query rather than
loading incidents and testing them in Go. An incident is due when it is
`status = 'triggered'`, has an escalation policy, has `escalation_status` in
(`none`, `pending`), and satisfies one of:

- **Never escalated** - `last_escalated_at IS NULL` and `created_at` is older
  than level 1's `timeout_minutes`.
- **Already escalated** - `last_escalated_at` is older than the *current*
  level's `timeout_minutes`, **and** a next level exists.

Two consequences follow. Timeouts are measured against the database clock, so
worker and database clock skew cannot mis-fire escalations. And requiring a next
level to exist means an incident at the final level is simply never selected.

### Concurrency safety

The query ends with `ORDER BY created_at ASC LIMIT 50 FOR UPDATE SKIP LOCKED`.

`FOR UPDATE SKIP LOCKED` is what makes it safe to run the in-process workers and
the standalone worker Deployment simultaneously - each row is claimed by exactly
one worker and the others skip past it rather than blocking. This is the
guarantee the Helm chart's `worker` component relies on (see
[deployment](../operations/deployment.md)). `LIMIT 50` bounds each pass; oldest
incidents go first.

Each selected incident is then processed in its **own goroutine**.

---

## Processing one escalation

`processIncidentEscalation` computes `nextLevel = current + 1`, where level `0`
means "not yet escalated", and terminates the chain in three cases, each marking
`escalation_status = 'completed'`:

- the policy has no levels;
- `nextLevel` exceeds the number of levels;
- the level number is not found among the defined levels.

Otherwise it dispatches on the level's `target_type`:

| Target type | Behaviour |
|---|---|
| `user` | Assign to that user |
| `scheduler` | Assign to whoever is on call in that scheduler |
| `current_schedule` | Assign via the **incident's own group** |
| `group` | Assign via the named group |
| `external` | External target |

Note that `current_schedule` ignores `level.TargetID` and uses
`incident.GroupID` instead - the level does not name the group, the incident
does.

### Overrides are respected automatically

`escalateToScheduler` queries the **`effective_shifts` view**, not the raw
`shifts` table, so a vacation swap or temporary override is honoured without the
worker knowing overrides exist. The view is where that resolution lives (see
[on-call scheduling](../workflows/oncall-scheduling.md)).

### Assignment versus notification

`escalateToUser` assigns with `sendNotification: false` and then sends an
**escalation** notification explicitly. The distinction is deliberate: the user
should be told "this was escalated to you", not "this was assigned to you".

A notification failure is logged but does **not** fail the assignment - the
incident is assigned either way, and the person can still find it in the UI.

On success the worker writes an incident event recording the level, target type
and target id, so the escalation path is reconstructable afterwards. A failure
leaves `escalation_status` as `pending`, and the comment notes no update is
needed because `FOR UPDATE SKIP LOCKED` already handles the concurrency -
retry happens on the next tick.

---

## Notification delivery

### Why Slack left the Go worker

The Go `NotificationWorker` carries an explicit note: **it no longer handles
Slack, which is delegated to the Python `SlackWorker`.** In the current code its
`incident_notifications` and `general_notifications` processing calls are
commented out, and it actively drains only `incident_actions`.

The reason is visible in what Slack work requires: Block Kit message
construction, interactive callbacks, per-incident message threading and the
`slack_bolt` SDK. That ecosystem is Python-native, and isolating it means a
Slack API outage or a slow Slack call stalls only its own consumer.

### The queue topology

```
 API / worker ──► incident_notifications ──┐
 Go worker    ──► slack_feedback ──────────┴─► Python SlackWorker ──► Slack
                                                        │
 Go worker    ◄── incident_actions ◄────────────────────┘  (button clicks)
```

Three queues, in two directions. The Go side produces onto
`incident_notifications` (a new notification) and `slack_feedback` (an update to
an already-posted message, for optimistic UI). The Python worker drains both and
produces onto `incident_actions` when a user clicks a Slack button, which the Go
worker drains and applies.

`LightweightNotificationSender` in the API process only enqueues -
`SELECT pgmq.send('incident_notifications', ...)` - so the HTTP request path
never waits on Slack or FCM. The payload carries type, user id, incident id,
`channels`, priority and a retry count.

### The Python consumer

`SlackWorker` polls on a configurable interval, reading batches from
`incident_notifications` and `slack_feedback`. Its handling is defensive at each
step:

- A message whose payload will not parse as JSON, or is not a dict, is **deleted
  immediately** rather than retried - a malformed message can never succeed, so
  retrying it would block the queue.
- `process_notification` checks the `channels` list and **returns success when
  `slack` is absent**, so a push-only notification is consumed rather than
  retried forever.
- On failure, `handle_failed_message` compares PGMQ's `read_ct` against
  `max_retries` and deletes the message once exceeded, otherwise leaves it for
  the visibility timeout to redeliver. This is the dead-letter behaviour.
- The main loop catches per-message exceptions, and a fatal loop error sleeps 10
  seconds before retrying rather than exiting.

A message is deleted **only after successful processing**, so a crashed worker
redelivers rather than loses.

---

## The other delivery channels

**Push (FCM).** `FCMService` uses the Firebase Admin SDK directly, and also
supports a **cloud relay** (`inres_CLOUD_URL`, token, instance id) for
self-hosted deployments that cannot hold Firebase credentials - the relay
forwards on their behalf.

**In-app realtime.** `RealtimeBroadcastService` posts to Supabase's Realtime
broadcast API with a channel, event and payload, which is what the frontend's
per-organization channel subscription receives (see
[web application](../frontend/web-application.md)). It is injected into
`IncidentService` via `SetBroadcastService` in both the router and
`cmd/server`.

**Slack (direct).** `SlackService` exists in the Go API for Block Kit messages
and is used by the notification handler's test endpoints, distinct from the
queue-driven worker path.

---

## Failure semantics, summarised

| Failure | Result |
|---|---|
| Escalation target fails | `escalation_status` stays `pending`; retried next tick |
| Notification send fails | Logged; assignment still stands |
| Slack message fails | Retried up to `max_retries`, then dropped |
| Malformed queue payload | Deleted immediately |
| Worker crashes mid-message | Redelivered after the visibility timeout |
| Policy has no next level | Chain marked `completed` |

The consistent bias is **toward assigning and recording the incident even when
telling someone about it fails** - a visible unnotified incident is recoverable,
a lost one is not.
