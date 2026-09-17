---
type: subsystem
title: Uptime Monitoring
description: How InRes probes services from Cloudflare's edge — the self-deploying Worker, its D1 storage and cron loop, the two paths by which a failed probe becomes an incident, and synchronization with UptimeRobot and Checkly.
tags: [uptime, monitoring, cloudflare-workers, d1, incidents, uptimerobot, checkly]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-03c7a851485f61b6fb348cc1
    resource: repo://server/api/internal/monitor/cloudflare_client.go
  - id: openwiki-source-27b082bba67d0dfae898e717
    resource: repo://server/api/internal/monitor/deployment_handler.go
  - id: openwiki-source-526f4dc0d38e358254dd75a0
    resource: repo://server/api/internal/monitor/report_handler.go
  - id: openwiki-source-abd99e0938f4999db0cee6a8
    resource: repo://server/api/internal/uptime/checkly_client.go
  - id: openwiki-source-8166218cadc6efcc6abf5ce2
    resource: repo://server/api/internal/uptime/provider_handler.go
  - id: openwiki-source-9fcc9e84ce9b75ec31606f7d
    resource: repo://server/api/internal/uptime/uptimerobot_client.go
  - id: openwiki-source-f0a4d687738282fced07298f
    resource: repo://worker/src/index.js
  - id: openwiki-source-daf36f188bf5e839a67e76ef
    resource: repo://worker/wrangler.toml
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Uptime Monitoring

Uptime checks do not run inside the InRes deployment. They run as a
**Cloudflare Worker at the edge**, which is the only way a probe can tell you
that your own cluster is unreachable.

The subsystem has three parts:

1. A **Worker** that probes on a cron schedule and stores results in D1.
2. A **deployment API** in the Go service that provisions that Worker into the
   user's own Cloudflare account.
3. A **provider sync** layer that pulls in monitors already configured in
   UptimeRobot or Checkly.

Related: [Architecture overview](../architecture/overview.md) ·
[Incident lifecycle](../workflows/incident-lifecycle.md) ·
[Alert ingestion](../workflows/alert-ingestion.md)

---

## The Worker

`wrangler.toml` configures a one-minute cron (`* * * * *`) and a single D1
binding. A comment states the storage decision plainly: **D1 is the only
storage — no KV needed, simpler and cheaper.**

### The scheduled loop

Each firing does four things:

1. Read active monitors: `SELECT * FROM monitors WHERE is_active = 1`. An empty
   or failed read returns early.
2. Probe every monitor **concurrently** with `Promise.all`, tagging results with
   the Worker's location.
3. Batch-insert the results into `monitor_logs` via `env.inres_DB.batch(...)`.
4. Report state changes upward.

Four check types are dispatched by `method`: `TCP_PING`, `DNS`, `CERT_CHECK`,
and HTTP for everything else.

HTTP checks support a per-monitor timeout (10 s default) enforced with
`AbortController`, custom headers (parsed defensively — a malformed header JSON
logs and falls back to empty rather than failing the check), optional request
bodies for non-GET/HEAD methods, and redirect following controlled by
`follow_redirect`. Success is `expect_status` when set, otherwise any 2xx.
Passing the status check is not the end: `response_keyword` and
`response_forbidden_keyword` then validate the body, so a service returning 200
with an error page is still marked down.

### State-change detection

`handleIncidentsViaWebhook` exists to prevent alert fatigue. For each result it
loads the previous state from D1 and sends a webhook **only on a transition**:

| Previous | Current | Action |
|---|---|---|
| up | down | `trigger` |
| down | up | `resolve` |
| down | down | nothing — "still DOWN, no webhook sent" |
| up | up | nothing |

A monitor with no history is assumed up, so the first failing check does fire a
trigger.

### The webhook payload

`sendWebhookEvent` emits the **generic webhook format** the Go API already
understands: `alert_name`, `severity` (`critical`/`info`), `status`
(`firing`/`resolved`), summary, description, labels (including
`source: 'uptime-monitor'`, the monitor id, URL, method and Worker location),
annotations, and `starts_at`.

The important field is `fingerprint: monitor.id`. That is what makes the
existing deduplication and auto-resolution in
[alert ingestion](../workflows/alert-ingestion.md) work for uptime events
without any special-casing — the uptime subsystem reuses the alert pipeline
rather than parallelling it.

### HTTP API and CDN caching

Besides the cron handler, the Worker serves `/health`, `/api/metrics`,
`/api/monitors` and `/api/monitors/:id`. Responses are stored in Cloudflare's
default cache and served from it on a hit, with an `X-Cache-Status` header of
`HIT` or `MISS`. Only successful responses are cached, and the write happens
under `ctx.waitUntil` so it does not delay the response. A code comment notes
that metrics are cached at the CDN edge with `s-maxage=60`, which is why no
caching layer was added in D1.

---

## Two reporting paths

The Worker can report upward in two ways, and the priority is explicit:

**`inres_WEBHOOK_URL`** — the preferred path. Events go through a normal
integration webhook and enter the standard alert pipeline.

**`FALLBACK_WEBHOOK_URL`** — used only when the primary is unset, and only for
monitors that are currently down.

A third path, `POST /monitors/report`, is **deprecated but retained for backward
compatibility**. It is handled by `ReportHandler.HandleReport` in the Go API,
which updates each monitor's `last_check_at`, `last_status`, `last_latency`,
`last_error` and `is_up`, then applies its own incident logic: on an up→down
transition it creates an incident titled `Monitor Down: {name}` with urgency
high, severity critical, source `uptime-monitor` and the monitor id stored in
`ExternalID`; on down→up it resolves the active incident found by that
`ExternalID`. A monitor with no prior state that reports down also creates an
incident.

Two caveats are visible in the code and worth knowing. `HandleReport` carries a
`TODO` noting that the `Authorization` header is **not yet validated** against
the deployment token. And `resolveIncident` falls back to a direct SQL query
because `ListIncidents` does not support filtering by `external_id` — the
comments in that function are an unresolved working note left in the source.

---

## Self-service Worker deployment

`POST /monitors/deploy` provisions the Worker into the **user's own Cloudflare
account**, which is why the request carries their account id and API token.

Input handling is defensive about the mistakes people actually make: whitespace
is trimmed, an accidental `Bearer ` prefix is stripped, the account id is
checked for exactly 32 hex characters with an error that explicitly warns
against passing a Zone ID, and a token under 20 characters is rejected with a
hint that a Global API Key is not an API Token. Authentication failures are
rewritten to name the three permissions actually required: `Workers Scripts:Edit`,
`D1:Edit` and `Account Settings:Read`.

The flow then gets **or creates** the `inres_DB` D1 database (reusing an
existing one rather than proliferating databases), ensures its schema, reads the
Worker script, uploads it with its bindings, and registers the cron trigger.

`CloudflareClient` wraps the Cloudflare REST API for all of this — KV namespace
and D1 database creation, D1 SQL execution and querying, Worker upload and
deletion, cron trigger creation, worker details and metrics, and subdomain
lookup. `GetDeploymentStats` surfaces those Worker metrics, and
`RedeployWorker`, `UpdateWorkerURL` (validated by `isValidWorkerURL`) and
`DeleteDeployment` complete the lifecycle. A deployment can be bound to an
integration, whose `webhook_url` is validated as active before use.

---

## External provider synchronization

Teams already running UptimeRobot or Checkly can register those as providers
instead of deploying a Worker.

`SyncProvider` dispatches to `syncUptimeRobot` or `syncCheckly`. Both
authenticate with stored credentials, list the provider's monitors, and
normalise them into InRes's own representation — `GetMonitorStatus` and
`GetMonitorType` map UptimeRobot's integer codes to names, `ParseUptimeRatios`
splits its combined uptime-ratio string into 1-day, 7-day, 30-day and all-time
figures, and `GetChecklyStatus` derives a status from a check's failure, error
and degraded flags.

Checkly needs two credentials rather than one, so `splitChecklyCredentials`
unpacks an API key and account id from a single stored value; `ValidateCredentials`
verifies them before a sync proceeds.

Synced monitors are scoped to an organization (`orgID` is threaded through both
sync functions). `ListExternalMonitors` returns provider-sourced monitors, and
`GetAllMonitors` merges them with self-hosted ones so the dashboard presents a
single view regardless of where a check actually runs.
