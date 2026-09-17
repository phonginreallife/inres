---
type: architecture
title: System Architecture
description: The processes that make up InRes — a Go API, a Python AI agent, a Slack worker, a Cloudflare uptime worker and a Next.js frontend — and why they coordinate through Postgres tables and PGMQ queues rather than calling each other.
tags: [architecture, services, pgmq, postgres, redis, workers, kong]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-a2371d6362e5db4bc834ad03
    resource: repo://CLAUDE.md
  - id: openwiki-source-b677feaf4d6390ae5d7e5506
    resource: repo://deploy/docker/docker-compose.yaml
  - id: openwiki-source-dbbf8a547bfeaaceea4edfe3
    resource: repo://server/agent/claude_agent_api_v1.py
  - id: openwiki-source-7101a6239e312a00b6d9e177
    resource: repo://server/agent/main.py
  - id: openwiki-source-25f1cf5511c8d3ea565bc762
    resource: repo://server/agent/utils/redis_client.py
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-0d58800e4a6061a65eeff1bb
    resource: repo://server/api/cmd/worker/main.go
  - id: openwiki-source-3b6b6b719c0d61055a052fb5
    resource: repo://server/api/internal/background/notification.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-9b6dbc33d6da6ba8d48fc24e
    resource: repo://server/api/services/incident.go
  - id: openwiki-source-95cb700da6b5ccaddcf27b7a
    resource: repo://server/slack-worker/slack_worker.py
  - id: openwiki-source-f0a4d687738282fced07298f
    resource: repo://worker/src/index.js
  - id: openwiki-source-daf36f188bf5e839a67e76ef
    resource: repo://worker/wrangler.toml
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# System Architecture

InRes is five deployable processes plus a database. The database is not merely
storage — it is the **integration bus**. Almost every cross-process interaction
happens through a Postgres table or a PGMQ queue rather than an HTTP call
between services.

Related: [Configuration](../architecture/configuration.md) ·
[Deployment](../operations/deployment.md) ·
[Escalation and notifications](../workflows/escalation-and-notifications.md) ·
[Agent session architecture](../ai-agent/session-architecture.md)

---

## The processes

| Process | Language | Port | Responsibility |
|---|---|---|---|
| **API** (`server/api`) | Go / Gin | 8080 | REST surface, webhooks, business logic, in-process workers |
| **Agent** (`server/agent`) | Python / FastAPI | 8002 | WebSocket chat backed by the Claude Agent SDK |
| **Slack worker** (`server/slack-worker`) | Python | — | Consumes the Slack notification queue |
| **Frontend** (`frontend/inres`) | Next.js 16 / React 19 | 3000 | Web UI |
| **Uptime worker** (`worker`) | Cloudflare Worker | — | Cron-driven probes from the edge |
| **Worker binary** (`server/api/cmd/worker`) | Go | — | Optional standalone queue consumer |

Kong sits in front on port 8000 as the single external entry point, routing to
the API, the frontend and the agent. See
[deployment](../operations/deployment.md).

Note that the actual layout is `server/api`, `server/agent`,
`server/slack-worker`, `frontend/inres` and `worker` — the repository's
top-level `CLAUDE.md` describes an older `api/`, `api/ai/`, `web/inres/` layout
that no longer matches the tree.

---

## Shared-database integration

Services do not discover or call each other for ordinary work. The pattern is
consistently:

> **Producer writes a row or enqueues a message. Consumer polls and acts.**

This shows up in several places:

- The API enqueues notification work onto PGMQ; the Go notification worker and
  the Python Slack worker each drain their own queue.
- The API enqueues AI analysis requests; the analytics consumer picks them up
  independently of the request that created them.
- The agent reads a user's MCP servers, memory, pre-approved tools and
  marketplaces straight from Postgres tables the frontend wrote. See
  [agent extensibility](../ai-agent/extensibility.md).
- The zero-trust verifier persists sessions, nonces and instance public keys to
  Postgres so a restart does not lose replay protection. See
  [security and tool approval](../ai-agent/security-and-tool-approval.md).

The payoffs are that any consumer can be scaled or restarted independently, work
survives a process restart because it is durable in the queue, and a service
being down delays work rather than losing it. The cost is that the schema is a
shared contract between languages, so changes to it must be coordinated across
Go, Python and TypeScript.

There are a few deliberate exceptions where a synchronous call is genuinely
needed: the agent's incident tools call the Go API over HTTP, and the agent's
zero-trust verifier fetches instance public keys from the API when its cache
misses.

---

## Why the API also runs workers

`cmd/server/main.go` starts the notification worker and the incident escalation
worker as goroutines **inside the API process**, and `cmd/worker/main.go` starts
the same two workers as a standalone binary. Both exist on purpose.

Running both from one image makes a single-container deployment work with no
extra orchestration — Docker Compose runs only the `api` service and escalation
still fires. When throughput or isolation demands it, the worker binary is
deployed separately and scaled on its own; the Helm chart exposes this as a
separate component.

Because PGMQ hands each message to exactly one reader, running both at once is
safe: they compete for messages rather than duplicating them.

### The lightweight sender

The split is visible in how the API and the worker satisfy the same
`NotificationSender` interface with different implementations.

In the API process, `NewLightweightNotificationSender` is installed. Its methods
build a notification payload and do exactly one thing:
`SELECT pgmq.send('incident_notifications', ...)`. It **enqueues without
processing** — the request path never blocks on Slack or FCM.

In the worker process, the full `NotificationWorker` is installed instead, and
it both enqueues and drains. Swapping the implementation behind one interface is
what lets the same `IncidentService` code run in both without knowing which
process it is in.

Two other collaborators are injected the same way: `SetBroadcastService` wires
in realtime broadcast for live UI updates, and `SetNotificationWorker` chooses
the sender.

---

## Startup and shutdown

Both Go entrypoints follow the same sequence: load config, open Postgres (fatal
if `database_url` is missing), `SET TIME ZONE 'UTC'` on the connection so time
handling is consistent regardless of server locale, then start workers as
goroutines under a `WaitGroup`.

Redis is genuinely optional in the API: it tries the configured URL, falls back
to probing `localhost:6379`, and continues with a `nil` client if neither
answers.

The API then runs Gin in a goroutine and selects on either a server error or
`SIGINT`/`SIGTERM`. The worker binary waits on the same signals. Its shutdown
path is honest about its limits — the code notes that workers stop when the main
goroutine exits and that a production system would want real graceful shutdown.

---

## The agent process

The agent is a FastAPI application whose entrypoint (`main.py`) re-exports the
app from `claude_agent_api_v1.py`. It serves two WebSocket endpoints —
`/ws/chat` (JWT) and `/ws/secure/chat` (zero-trust signed envelopes) — plus REST
routers for conversations, audit, MCP, plugins and memory.

Its distinguishing property is that **one Claude Agent SDK client stays
connected for the life of a WebSocket**, planning, running tools and streaming
tokens in a single pass. That design and its constraints are covered in
[session architecture](../ai-agent/session-architecture.md).

Redis is used here for horizontal scaling: it backs the rate limiter and the
session store, so multiple agent replicas can share state.

---

## The edge uptime worker

The uptime prober is a **Cloudflare Worker**, not a service in the compose
stack. `wrangler.toml` gives it a one-minute cron trigger and a D1 database
binding; its `scheduled` handler reads active monitors from D1, probes them
concurrently, and batch-inserts results back into D1 before reporting upward.

Running checks at the edge is what makes them independent of the InRes
deployment itself — a probe from inside the same cluster cannot tell you the
cluster is unreachable. See [uptime monitoring](../monitoring/uptime-monitoring.md).

---

## Container topology

Compose declares two networks: `inres-network` for InRes's own services, and an
**external** Supabase network so the API can reach Postgres. The Supabase CLI
names that network after `project_id` in `supabase/config.toml`, so the compose
file makes it overridable through `SUPABASE_NETWORK`.

Two volumes matter for correctness rather than convenience:

- `claude_data` → `/root/.claude`
- `agent_workspaces` → `/app/workspaces`

Without the workspace volume, every rebuild wipes synced memory, activated
skills and cloned marketplaces **while their rows stay in Postgres** — the UI
then shows plugins as installed with their files gone. The Helm chart always had
a PVC here; Compose did not, and this volume is the fix.

The agent container carries explicit memory limits (2 GB limit, 512 MB
reservation), which is the counterpart to the `max_concurrent_cli` setting that
bounds live CLI subprocesses.
