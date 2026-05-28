# <img src="images/icon.svg" alt="InRes" width="24" height="24"/> InRes

### AI-Native Incident Response Platform

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Go](https://img.shields.io/badge/Go-1.21+-00ADD8?logo=go)](https://go.dev)
[![Next.js](https://img.shields.io/badge/Next.js-15-black?logo=next.js)](https://nextjs.org)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)](https://python.org)

InRes is an open-source on-call and incident management platform with an integrated AI agent that can investigate, diagnose, and help remediate issues - with human-in-the-loop approval for sensitive actions.

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
git clone https://github.com/phonginreallife/InRes.git
cd InRes

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

## Architecture

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
     ┌──────▼──────────────────────┐
     │        Supabase             │
     │  (PostgreSQL + Auth + PGMQ) │
     └─────────────────────────────┘
```

---

## Development

### Backend (Go)
```bash
cd api
go install github.com/cosmtrek/air@latest
air  # Hot reload on http://localhost:8080
```

### AI Agent (Python)
```bash
cd api/ai
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python claude_agent_api_v1.py  # http://localhost:8002
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
WS     /ws/chat                AI chat (block mode)
WS     /ws/stream              AI chat (streaming mode)
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
