# <img src="images/icon.svg" alt="InRes" width="24" height="24"/> InRes

### AI-Native Incident Response Platform

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Go](https://img.shields.io/badge/Go-1.24-00ADD8?logo=go)](https://go.dev)
[![Next.js](https://img.shields.io/badge/Next.js-16-black?logo=next.js)](https://nextjs.org)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)](https://python.org)
[![Docs](https://img.shields.io/badge/docs-phonginreallife.github.io%2Finres-0066CC)](https://phonginreallife.github.io/inres/)

InRes is an open-source on-call and incident management platform with an integrated AI agent that can investigate, diagnose, and help remediate issues - with human-in-the-loop approval for sensitive actions.

**📖 [Read the documentation](https://phonginreallife.github.io/inres/)** &nbsp;·&nbsp;
[Quickstart](https://phonginreallife.github.io/inres/wiki/quickstart.html) &nbsp;·&nbsp;
[Architecture](https://phonginreallife.github.io/inres/wiki/architecture/overview.html) &nbsp;·&nbsp;
[Interactive diagram](https://phonginreallife.github.io/inres/diagram.html)

<p align="center">
  <img src="images/dashboard.png" alt="InRes Dashboard" width="800"/>
</p>

---

## Features

### Incident Management
- **Full Lifecycle** - Triggered → Acknowledged → Resolved with audit trail
- **Smart Deduplication** - Fingerprint-based alert grouping
- **Auto-Resolution** - Recovery alerts automatically resolve incidents
- **Priority Mapping** - P1-P5 compatible with PagerDuty/Datadog

### On-Call & Escalation
- **Visual Scheduling** - Interactive timeline with drag-and-drop
- **Rotation Management** - Weekly, daily, custom rotations
- **Schedule Overrides** - Vacation swaps, temporary changes
- **Multi-Level Escalation** - Time-based escalation chains
- **Auto-Assignment** - Route incidents to the right person

### AI Agent (Claude-Powered)
- **Real-time Chat** - WebSocket streaming responses
- **Tool Execution** - Query systems, run commands, analyze logs
- **Human-in-the-Loop** - Approve sensitive actions before execution
- **Memory & Context** - Persistent conversations across sessions
- **MCP Integration** - Extensible tool ecosystem

### Integrations

**Alerting Sources**
- Prometheus/AlertManager
- Datadog
- Grafana
- AWS CloudWatch
- PagerDuty
- Coralogix
- Generic Webhook

**Uptime Monitoring**
- HTTP/HTTPS health checks
- Response time tracking
- SSL certificate monitoring
- UptimeRobot, Cloudflare Workers, Checkly

**Communication**
- Slack (interactive notifications)

### Multi-Tenancy
- Organizations & Projects
- Role-Based Access (Owner, Admin, Member, Viewer)
- Relationship-Based Access Control (ReBAC)
- JWT authentication via Supabase

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Anthropic API key (for AI features)

### 1. Clone & Configure

```bash
git clone https://github.com/phonginreallife/inres.git
cd inres

# Setup deployment directory
mkdir -p ../inres-project
cp -r deploy/docker ../inres-project/
```

### 2. Create Environment File

```bash
cat > ../inres-project/docker/.env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-your-key
DATABASE_URL=postgresql://postgres:postgres@supabase_db_supabase:5432/postgres
EOF
```

> **Two things catch people out.** `.env` must sit **next to the compose file** -
> Compose reads it from the directory containing the compose file, not your shell's
> working directory. And supply **exactly one** Anthropic credential: an API key
> takes precedence wherever it is found, so to use `CLAUDE_CODE_OAUTH_TOKEN` you
> must also blank `anthropic_api_key` in the config YAML below.

### 3. Configure Application

```bash
cp ../inres-project/docker/volumes/config/cfg.ex.yaml \
   ../inres-project/docker/volumes/config/dev.config.yaml

# Edit dev.config.yaml with your Supabase credentials
```

### 4. Start Services

```bash
cd ../inres-project/docker
docker compose up -d
```

### 5. Access

| Service | URL |
|---------|-----|
| **Frontend** | http://localhost:8000 |
| **API** | http://localhost:8080 |
| **AI Agent** | http://localhost:8002 |

---

## Documentation

**<https://phonginreallife.github.io/inres/>**

The documentation is **generated from the source by [OpenWiki](https://github.com/openwiki)**
and lives in [`openwiki/`](openwiki/). Every substantive statement is a *claim*
bound to the code span that supports it, and each span is fingerprinted by
content hash - so when the code moves, the affected sentences are flagged rather
than quietly rotting. A scheduled workflow refreshes the pages and redeploys the
site.

| Start here | For |
|------------|-----|
| [Quickstart](https://phonginreallife.github.io/inres/wiki/quickstart.html) | Task-routing map, and the behaviours that are deliberate but surprising |
| [System architecture](https://phonginreallife.github.io/inres/wiki/architecture/overview.html) | How the five processes fit together |
| [Alert ingestion](https://phonginreallife.github.io/inres/wiki/workflows/alert-ingestion.html) | Adding a monitoring integration |
| [Escalation](https://phonginreallife.github.io/inres/wiki/workflows/escalation-and-notifications.html) | Working out why nobody got paged |
| [Agent session architecture](https://phonginreallife.github.io/inres/wiki/ai-agent/session-architecture.html) | Changing the AI agent |
| [Deployment](https://phonginreallife.github.io/inres/wiki/operations/deployment.html) | Deploying or scaling |

Generated pages are overwritten on each refresh - change the code they describe,
not the page.

---

## Architecture

An [interactive diagram](https://phonginreallife.github.io/inres/diagram.html) is
also available (pan, zoom, trace relationships).

```
                    ┌─────────────────┐
                    │   Frontend      │
                    │   (Next.js)     │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Kong Gateway  │
                    │     :8000       │
                    └────────┬────────┘
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼────┐  ┌──────▼──────┐  ┌────▼────────┐
     │   Go API    │  │  AI Agent   │  │   Workers   │
     │   :8080     │  │   :8002     │  │  (Slack,    │
     └──────┬──────┘  └──────┬──────┘  │  Escalation)│
            │                │         └─────────────┘
            │         ┌──────▼──────┐
            │         │  Anthropic  │
            │         │  Claude API │
            │         └─────────────┘
     ┌──────▼──────────────────────┐        ┌──────────────────┐
     │        Supabase             │◄───────│ Cloudflare Worker│
     │  (PostgreSQL + Auth + PGMQ) │        │  (uptime probes) │
     └─────────────────────────────┘        └──────────────────┘
```

Services coordinate through Postgres tables and PGMQ queues rather than calling
each other: a producer enqueues durable work, an independent consumer drains it.

---

## Development

### Repository layout

| Path | What it is |
|------|------------|
| `server/api` | Go / Gin API - REST, webhooks, routing, escalation, background workers |
| `server/agent` | Python / FastAPI AI agent - WebSocket chat on the Claude Agent SDK |
| `server/slack-worker` | Python Slack notification consumer |
| `frontend/inres` | Next.js web UI |
| `worker` | Cloudflare Worker for edge uptime probing |
| `deploy` | Docker Compose, Helm chart, deployment CLI |
| `supabase/migrations` | Database schema, in timestamp order |
| `openwiki` | Generated documentation - see [Documentation](#documentation) |

### Backend (Go)
```bash
cd server/api
go install github.com/air-verse/air@latest
air  # Hot reload on http://localhost:8080
```

### AI Agent (Python)
```bash
cd server/agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
make dev  # uvicorn with reload on http://localhost:8002
```

### Frontend (Next.js)
```bash
cd frontend/inres
npm install
npm run dev  # http://localhost:3000
```

### Database
```bash
cd supabase
supabase link
supabase db push
```

### Tests

Only the Go suite blocks CI; the frontend and agent jobs run `continue-on-error`,
so run those locally before pushing.

```bash
cd server/api     && go test -race ./...
cd server/agent   && pytest
cd frontend/inres && npm run lint && npm run build
```

---

## Webhook Configuration

Configure your monitoring tools to send alerts to InRes:

```
POST /webhook/{provider}/{integration_id}
```

| Provider | Webhook Path |
|----------|--------------|
| Prometheus | `/webhook/prometheus/{id}` |
| Datadog | `/webhook/datadog/{id}` |
| Grafana | `/webhook/grafana/{id}` |
| AWS CloudWatch | `/webhook/aws/{id}` |
| PagerDuty | `/webhook/pagerduty/{id}` |
| Coralogix | `/webhook/coralogix/{id}` |
| Generic | `/webhook/webhook/{id}` |

**Example: Prometheus AlertManager**
```yaml
receivers:
  - name: 'inres'
    webhook_configs:
      - url: 'https://your-domain/webhook/prometheus/YOUR_ID'
        send_resolved: true
```

---

## API Reference

### Incidents
```
GET    /incidents              List incidents
POST   /incidents              Create incident
GET    /incidents/:id          Get incident
PUT    /incidents/:id/ack      Acknowledge
PUT    /incidents/:id/resolve  Resolve
```

### Schedules
```
GET    /schedules              List schedules
GET    /schedules/timeline     Get timeline
POST   /overrides              Create override
```

### Uptime
```
GET    /uptime/services        List monitors
POST   /uptime/services        Create monitor
GET    /uptime/services/:id    Get status
GET    /uptime/dashboard       Dashboard data
```

### AI Agent
```
WS     /ws/chat                AI chat (token streaming)
WS     /ws/secure/chat         AI chat (zero-trust signed)
GET    /conversations          List conversations
GET    /mcp/servers            List MCP tools
```

---

## Security

- **Authentication** - Supabase JWT with RS256/ES256 verification
- **Authorization** - ReBAC for fine-grained tenant isolation
- **AI Safety** - Human approval required for sensitive tool execution
- **Audit Trail** - Complete logging of all actions and AI operations
- **SQL Injection** - Parameterized queries throughout

---

## Roadmap

- [x] Incident management with lifecycle
- [x] On-call scheduling & escalations
- [x] AI-powered investigation assistant
- [x] 7+ monitoring integrations
- [x] Uptime monitoring with SSL tracking
- [x] Multi-tenant organizations
- [x] Token-level AI streaming
- [ ] Advanced routing rules (regex, CEL)
- [ ] Runbook automation
- [ ] Mobile app
- [ ] Public status pages
- [ ] Post-mortem templates

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
# Fork, then:
git checkout -b feature/your-feature
git commit -m "Add your feature"
git push origin feature/your-feature
# Open a Pull Request
```

---

## License

[AGPLv3](LICENSE) - Self-host freely, no vendor lock-in.

---

## Acknowledgements

- [slar](https://github.com/SlarOps/slar) - Original inspiration
- [Anthropic Claude](https://anthropic.com) - AI capabilities
- [Supabase](https://supabase.com) - Auth & Database

---

<p align="center">
  <strong>Built for SREs and DevOps teams who are tired of alert fatigue.</strong>
</p>
