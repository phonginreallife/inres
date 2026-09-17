---
title: Home
layout: home
nav_order: 1
description: "InRes — an open-source, AI-native on-call and incident response platform."
permalink: /
---

# InRes
{: .fs-9 }

Open-source on-call and incident management, with an AI agent that investigates
alongside you — and asks before it touches anything.
{: .fs-6 .fw-300 }

[Get started](#running-it-locally){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[Architecture diagram](architecture.html){: .btn .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View on GitHub](https://github.com/phonginreallife/InRes){: .btn .fs-5 .mb-4 .mb-md-0 }

---

## What it does

An alert arrives by webhook. It becomes an incident, gets assigned to whoever is
actually on call, and escalates if nobody responds. At any point you can ask an
AI agent to investigate — it has read access to your incidents and whatever
tools you have given it, and it asks for approval before running anything
sensitive.

<div class="code-example" markdown="1">

**Incident management** — full lifecycle with an append-only audit trail,
fingerprint-based deduplication, automatic resolution on recovery, and P1–P5
priorities compatible with PagerDuty and Datadog.

**On-call and escalation** — visual scheduling, rotations, overrides for
vacation swaps, and multi-level time-based escalation chains.

**AI agent** — real-time streaming chat over WebSocket, human-in-the-loop tool
approval, persistent conversation context, and an extensible MCP tool ecosystem.

**Integrations** — Prometheus/Alertmanager, Datadog, Grafana, AWS CloudWatch,
PagerDuty, Coralogix, generic webhooks, Slack, and edge uptime monitoring.

**Multi-tenancy** — organizations and projects with relationship-based access
control.

</div>

---

## The documentation

These pages are **generated from the repository by
[OpenWiki](https://github.com/openwiki)** and regenerated when the source
changes. Every substantive claim is backed by a citation to the code that
supports it, so the docs describe what the system actually does rather than what
it was intended to do.

New here? Start with the **[Quickstart](wiki/quickstart.html)** — it includes a
task-routing map ("I want to do X → read page Y") and a list of behaviours that
are deliberate but surprising.

| If you want to… | Read |
|:--|:--|
| Understand the system | [System architecture](wiki/architecture/overview.html) |
| Add a monitoring integration | [Alert ingestion and routing](wiki/workflows/alert-ingestion.html) |
| Work out why nobody was paged | [Escalation and notifications](wiki/workflows/escalation-and-notifications.html) |
| Fix who gets paged | [On-call scheduling](wiki/workflows/oncall-scheduling.html) |
| Change the AI agent | [Session architecture](wiki/ai-agent/session-architecture.html) |
| Deploy or scale it | [Deployment and operations](wiki/operations/deployment.html) |

---

## Running it locally

```bash
git clone https://github.com/phonginreallife/InRes.git
cd InRes/deploy/docker
cp ../../.env.example .env        # then fill it in
docker compose up -d
```

| Service | URL |
|:--|:--|
| Web UI | <http://localhost:8000> |
| API | <http://localhost:8080> |
| AI agent | <http://localhost:8002> |

Two things catch people out, both covered in
[configuration](wiki/architecture/configuration.html):

{: .warning }
> `.env` must sit **next to the compose file**, not in your shell's working
> directory — Compose reads it from the directory containing the compose file.
>
> Supply **exactly one** Anthropic credential. An API key takes precedence
> wherever it is found, so to use `CLAUDE_CODE_OAUTH_TOKEN` you must also blank
> `anthropic_api_key` in the config YAML.

---

## The shape of it

Five processes and a database. The database is not just storage — it is the
integration bus, and services coordinate through Postgres tables and PGMQ queues
rather than calling each other.

| Process | Language | Responsibility |
|:--|:--|:--|
| `server/api` | Go | REST, webhooks, routing, escalation, background workers |
| `server/agent` | Python | WebSocket chat on the Claude Agent SDK |
| `server/slack-worker` | Python | Slack delivery and interactions |
| `frontend/inres` | Next.js | Web UI |
| `worker` | Cloudflare Worker | Edge uptime probing |

See the [full architecture](wiki/architecture/overview.html), or the
[interactive diagram](architecture.html).

---

## Contributing

Issues and pull requests are welcome — see
[CONTRIBUTING.md](https://github.com/phonginreallife/InRes/blob/main/CONTRIBUTING.md).
If you are changing behaviour these docs describe, regenerate them with OpenWiki
in the same pull request so the citations stay accurate.
