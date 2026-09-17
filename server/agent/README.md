# InRes AI Agent

AI-powered incident response assistant built with FastAPI and Claude SDK.

## Overview

The InRes Agent provides an intelligent conversational interface for incident management, leveraging Claude's capabilities with custom tools for incident response workflows.

## Architecture

Each WebSocket connection owns one `ChatSession`, which holds a single Claude
Agent SDK client open for the life of the conversation. That one client does the
planning, runs the tools and streams tokens — so context survives between turns,
and there is no second model call to produce the streaming text.

```
agent/
├── main.py                    # Entry point (uvicorn)
├── claude_agent_api_v1.py     # FastAPI app + WebSocket endpoints
├── ws_chat.py                 # Shared connection driver (queue, persistence)
├── errors.py                  # Error sanitisation
├── session/                   # The agent
│   ├── session.py            # ChatSession: client lifecycle + turn loop
│   ├── translate.py          # SDK messages -> WebSocket events
│   ├── permissions.py        # Tool approval broker
│   ├── events.py             # Every event payload, in one place
│   ├── config.py             # SessionConfig -> ClaudeAgentOptions
│   └── _sdk_types.py         # SDK types, with a shim for tests
├── streaming/                 # MCP client pool
│   ├── mcp_client.py         # MCP client integration
│   └── mcp_config.py         # MCP configuration
├── routes/                    # REST API endpoints
│   ├── conversations.py      # Chat history
│   ├── audit.py              # Audit logs
│   ├── mcp.py                # MCP management
│   ├── memory.py             # Agent memory
│   └── marketplace.py        # Plugin marketplace
├── tools/                     # Agent tool definitions
│   └── incidents.py          # Incident management tools
├── services/                  # Business logic
├── security/                  # Zero-trust verification
├── audit/                     # Security audit logging
├── config/                    # Configuration loader
├── tests/                     # Unit tests (no API key or CLI needed)
└── utils/                     # Utilities
```

## Endpoints

| Endpoint | Type | Description |
|----------|------|-------------|
| `/ws/chat` | WebSocket | Chat, authenticated by JWT query param |
| `/ws/secure/chat` | WebSocket | Same, with zero-trust signed messages |
| `/api/*` | REST | Conversations, audit, MCP, plugins, memory |

### `/ws/chat` protocol

Connect with `?token=<jwt>&org_id=…&project_id=…`, optionally
`&conversation_id=…` to resume a previous conversation.

Client sends:

| Message | Purpose |
|---------|---------|
| `{"prompt": "...", "conversation_id": "..."}` | Ask something |
| `{"type": "interrupt"}` | Stop the turn in flight |
| `{"type": "clear_history"}` | Start a fresh conversation |
| `{"type": "permission_response", "request_id": "...", "allow": "yes"\|"no"}` | Answer a tool approval |
| `{"type": "pong"}` | Reply to a heartbeat |

Server sends `session_created`, then per turn: `processing`, `session_init`,
`delta`, `thinking`, `tool_use`, `tool_result`, `todo_update`,
`permission_request` / `permission_timeout`, and exactly one of `complete`,
`error` or `interrupted`. Plus `ping` every 30s and `history_cleared` on reset.

## Configuration

The agent reads the `ai_agent` section of the shared config YAML; every key can
be overridden with an `AI_AGENT_*` environment variable. See
`deploy/docker/volumes/config/cfg.ex.yaml` for the annotated list — the ones
worth knowing about are `model`, `require_tool_approval`, `idle_timeout_s` and
`max_concurrent_cli` (one CLI subprocess per active session).

## Tests

```bash
make test          # unit tests: no API key, no CLI, no database
make test-ws TOKEN=<jwt>   # end-to-end against a running server
```

## Tech Stack

- **Framework**: FastAPI + Uvicorn
- **AI**: Claude SDK (`claude-agent-sdk`), Anthropic API
- **Protocol**: MCP (Model Context Protocol)
- **Database**: PostgreSQL (via Supabase)
- **Auth**: JWT tokens

## Quick Start

### Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set config path (shared with Go API)
export inres_CONFIG_PATH=../api/cmd/server/dev.config.yaml

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `inres_CONFIG_PATH` | Path to config YAML | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `SUPABASE_URL` | Supabase instance URL | - |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service key | - |
| `DATABASE_URL` | PostgreSQL connection string | - |
| `PORT` | Server port | `8002` |
| `USER_WORKSPACES_DIR` | User workspace directory | `/app/workspaces` |

### Docker

```bash
# Build image
docker build -t inres-agent .

# Run container
docker run -p 8002:8002 \
  -e ANTHROPIC_API_KEY=your-key \
  -e inres_CONFIG_PATH=/app/config.yaml \
  -v ./config.yaml:/app/config.yaml \
  inres-agent
```

## Features

- **Incident Tools**: Create, update, and manage incidents via natural language
- **MCP Integration**: Connect to external tools via Model Context Protocol
- **Streaming**: Real-time token-level response streaming
- **Memory**: Persistent conversation memory and context
- **Audit Logging**: Security audit trail for all agent actions
- **Zero-Trust Security**: Device certificate verification
- **Plugin Marketplace**: Extensible plugin system

## Development

### Project Structure

- `streaming/` - New token-level streaming implementation (preferred)
- `claude_agent_api_v1.py` - Legacy block-based implementation (compatibility)

### Adding New Tools

Tools are defined in `tools/` and exposed via MCP:

```python
# tools/incidents.py
@mcp_server.tool()
async def create_incident(title: str, severity: str, ...):
    """Create a new incident."""
    # Implementation
```

### Running Tests

```bash
pytest tests/
```

## Related Services

- **inres-api** (`:8080`) - Go backend API
- **inres-frontend** (`:3000`) - Next.js web UI
- **inres-slack-worker** - Slack integration worker
