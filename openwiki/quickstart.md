---
type: guide
title: InRes Quickstart
description: What InRes is, which process owns which responsibility, where the code actually lives, and which wiki page answers which kind of task.
tags: [quickstart, orientation, onboarding, repository-layout, navigation]
sources:
  - id: openwiki-source-1f5556594237f9562185646d
    resource: repo://.github/workflows/build.yaml
  - id: openwiki-source-eb8db7eaad74c9ff88a54cd3
    resource: repo://.github/workflows/test.yaml
  - id: openwiki-source-a2371d6362e5db4bc834ad03
    resource: repo://CLAUDE.md
  - id: openwiki-source-b677feaf4d6390ae5d7e5506
    resource: repo://deploy/docker/docker-compose.yaml
  - id: openwiki-source-3847fa5c659f0a1dcc0ec452
    resource: repo://deploy/docker/volumes/api/kong.yaml
  - id: openwiki-source-cd41e395d77cca0775770256
    resource: repo://deploy/helm/inres/templates/hpa.yaml
  - id: openwiki-source-0f4ac35ea5330167c567f09e
    resource: repo://deploy/helm/inres/values.yaml
  - id: openwiki-source-47d02fca3524898d5aae2b3b
    resource: repo://LICENSE
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-28c25db462e264c7cff861de
    resource: repo://server/agent/config/loader.py
  - id: openwiki-source-9432d280030ee1b71985428a
    resource: repo://server/api/.air.toml
  - id: openwiki-source-349b953ef4310fbbf38c78ea
    resource: repo://server/api/authz/simple.go
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-47096cf7b0e2deed96fdd235
    resource: repo://server/api/internal/config/config.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-9b6dbc33d6da6ba8d48fc24e
    resource: repo://server/api/services/incident.go
  - id: openwiki-source-0a0fe3844e6ccd74f5a10cba
    resource: repo://server/api/services/oncall.go
  - id: openwiki-source-95cb700da6b5ccaddcf27b7a
    resource: repo://server/slack-worker/slack_worker.py
  - id: openwiki-source-2496dff3225cab4e508a0f60
    resource: repo://supabase/migrations/20251017163508_create_effective_shifts_view.sql
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T11:09:25.960Z
---

# InRes Quickstart

InRes is an open-source on-call and incident management platform with an
integrated AI agent that can investigate and help remediate issues, with human
approval required for sensitive actions. It is licensed AGPLv3.

The shape of the system in one line: **alerts arrive by webhook, become
incidents, get assigned to whoever is on call, escalate if nobody responds, and
an AI agent can be asked to investigate.**

---

## Repository layout

The tree does **not** match the layout described in the root `CLAUDE.md`, which
still refers to `api/`, `api/ai/` and `web/inres/`. The real directories are:

| Path | What it is |
|---|---|
| `server/api` | Go / Gin API - REST, webhooks, business logic, background workers |
| `server/agent` | Python / FastAPI AI agent - WebSocket chat on the Claude Agent SDK |
| `server/slack-worker` | Python Slack notification consumer |
| `frontend/inres` | Next.js 16 / React 19 web UI |
| `worker` | Cloudflare Worker for edge uptime probing |
| `deploy` | Docker Compose, Helm chart, deployment CLI |
| `supabase/migrations` | The schema, in timestamp order |

Ports: API **8080**, agent **8002**, frontend **3000**, Kong **8000**.

---

## Who owns what

| Responsibility | Owner |
|---|---|
| REST API, webhook ingestion, routing, escalation | `server/api` |
| Escalation and notification workers | `server/api` (in-process **and** a standalone binary) |
| AI chat, tools, MCP, tool approval | `server/agent` |
| Slack message delivery and interactions | `server/slack-worker` |
| Uptime probing | `worker` (Cloudflare edge) |
| Auth, database, realtime, storage | Supabase |

The thing to internalise early: **services do not call each other for ordinary
work.** They coordinate through Postgres tables and PGMQ queues. A producer
enqueues; an independent consumer drains. See
[architecture overview](./architecture/overview.md).

---

## Running it

```bash
cd deploy/docker
docker compose up -d
```

Two things bite newcomers, both documented in the compose file itself:

- **`.env` must sit next to the compose file**, not in your shell's working
  directory.
- **Supply exactly one Anthropic credential.** An API key wins wherever it is
  found, so to use `CLAUDE_CODE_OAUTH_TOKEN` you must also blank
  `anthropic_api_key` in `deploy/docker/volumes/config/dev.config.yaml`.

Both the Go API and the Python agent read the **same** YAML config file. See
[configuration](./architecture/configuration.md).

---

## Where to go next

**"I want to understand the system."**
→ [Architecture overview](./architecture/overview.md), then
[configuration](./architecture/configuration.md).

**"I'm adding a monitoring integration."**
→ [Alert ingestion and routing](./workflows/alert-ingestion.md) - provider
normalization, the common severity/status vocabulary, and deduplication.

**"An alert didn't page anyone."**
→ [Alert ingestion](./workflows/alert-ingestion.md) for service and assignee
resolution, then
[escalation and notifications](./workflows/escalation-and-notifications.md) for
the escalation loop's timeout logic.

**"The wrong person was paged."**
→ [On-call scheduling and rotations](./workflows/oncall-scheduling.md) -
especially override precedence and which code paths bypass it.

**"Slack notifications aren't arriving."**
→ [Escalation and notifications](./workflows/escalation-and-notifications.md) -
the queue topology and the Python consumer's retry behaviour.

**"I'm working on incident state or the timeline."**
→ [Incident lifecycle](./workflows/incident-lifecycle.md).

**"I'm changing the AI agent."**
→ [Session architecture](./ai-agent/session-architecture.md) first - the task
layout and turn serialisation constrain almost every change. Then
[streaming protocol](./ai-agent/streaming-protocol.md) for the WebSocket
contract.

**"I'm adding an agent tool or MCP server."**
→ [Agent extensibility](./ai-agent/extensibility.md).

**"I'm touching tool approval or the zero-trust socket."**
→ [Tool approval, zero trust and audit](./ai-agent/security-and-tool-approval.md).
Read the deadlock-freedom argument before changing anything in that path.

**"I'm working on auth or login."**
→ [Authentication and identity](./concepts/authentication-and-identity.md).

**"A user can't see data they should."**
→ [Multi-tenancy and authorization](./concepts/tenancy-and-authorization.md) -
role matrices and the conditional org-to-project inheritance rule.

**"I'm changing the schema."**
→ [Data model and migrations](./operations/data-and-migrations.md). The schema
is a contract shared across Go, Python and TypeScript.

**"I'm working on the UI."**
→ [Web application](./frontend/web-application.md).

**"I'm deploying or scaling."**
→ [Deployment and operations](./operations/deployment.md).

**"I'm on uptime monitoring."**
→ [Uptime monitoring](./monitoring/uptime-monitoring.md).

**"My PR is failing CI."**
→ [CI pipelines and testing](./development/ci-and-testing.md). Note that only
the Go test job actually blocks - frontend and agent tests run
`continue-on-error`.

---

## Things that will surprise you

A short list of behaviours that are deliberate but non-obvious, each covered in
detail on its page:

- **The webhook endpoint is public.** The integration id in the URL is the
  credential.
- **Escalation already runs with zero worker replicas** - the API server starts
  the workers in-process. The standalone worker exists to move that load off the
  API pods.
- **The agent cannot be horizontally scaled.** The Helm chart hard-fails if you
  try, because conversation resume reads a pod-local transcript.
- **Adding the first explicit member to a project stops it inheriting access
  from its organization** - which silently removes access from everyone who had
  it by inheritance.
- **Override resolution lives in a database view.** Code that queries `shifts`
  directly bypasses overrides, and some of it does.
- **One YAML file configures both the Go and Python services**, and both copy
  their resolved config back into the process environment.

---

## Local development

```bash
# Go API (hot reload)
cd server/api && air

# AI agent
cd server/agent && make dev

# Frontend
cd frontend/inres && npm run dev

# Migrations
cd supabase && supabase db push
```

Before pushing, run the suites CI does not enforce:

```bash
cd server/api && go test -race ./...
cd server/agent && pytest
cd frontend/inres && npm run lint && npm run build
```
