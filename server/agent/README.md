# InRes AI Agent

AI-powered incident response assistant built with FastAPI, Claude SDK, and persistent memory.

## Overview

The InRes Agent provides an intelligent conversational interface for incident management, leveraging Claude's capabilities with custom tools for incident response workflows. It features a hybrid architecture combining SDK-based tool orchestration with token-level streaming and claude-mem inspired persistent memory.

## Architecture

```
agent/
├── main.py                    # Entry point (uvicorn)
├── claude_agent.py            # Main WebSocket API handler
│
├── core/                      # Core abstractions
│   ├── base_agent.py          # Abstract agent interface
│   ├── lifecycle_hooks.py     # Memory lifecycle hooks (claude-mem style)
│   ├── message_history.py     # Conversation history management
│   └── tool_executor.py       # Unified tool execution
│
├── hybrid/                    # SDK + Streaming hybrid approach
│   ├── sdk_agent.py           # SDKHybridAgent (recommended)
│   ├── sdk_orchestrator.py    # SDK planning layer
│   ├── agent.py               # Legacy hybrid agent
│   └── orchestrator.py        # Legacy orchestrator
│
├── streaming/                 # Direct API streaming
│   ├── agent.py               # StreamingAgent implementation
│   ├── mcp_client.py          # MCP server client
│   └── mcp_config.py          # MCP configuration
│
├── services/                  # Business logic services
│   ├── memory_service.py      # Persistent memory (observations, summaries)
│   ├── context_retriever.py   # Progressive disclosure
│   ├── vector_store.py        # ChromaDB vector search (optional)
│   ├── storage.py             # Supabase storage utilities
│   └── analytics.py           # Usage analytics
│
├── routes/                    # REST API endpoints
│   ├── conversations.py       # Chat history CRUD
│   ├── memory.py              # CLAUDE.md memory
│   ├── mcp.py                 # MCP server management
│   ├── marketplace.py         # Plugin marketplace
│   ├── tools.py               # Tool management
│   ├── db.py                  # Database utilities
│   └── sync.py                # Workspace sync
│
├── tools/                     # Agent tool definitions
│   └── incidents.py           # Incident management tools
│
├── security/                  # Security components
│   └── zero_trust_verifier.py # Device certificate verification
│
├── audit/                     # Security audit
│   ├── audit_service.py       # Audit logging service
│   └── audit_hooks.py         # Audit event hooks
│
├── config/                    # Configuration
│   └── config_loader.py       # YAML config loader
│
└── utils/                     # Utilities
    ├── database.py            # PostgreSQL utilities
    └── git_utils.py           # Git operations
```

## Agent Architecture

The agent supports three modes of operation:

### 1. SDK Hybrid Agent (Recommended)

Combines Claude SDK for planning/tools with direct API for streaming:

```
User Message
     │
     ▼
┌─────────────────────────────────────────────────┐
│         Lifecycle Hooks (Memory)                 │
│  on_prompt_submit() → Inject relevant memories  │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           SDK Orchestrator (Call #1)            │
│  • Planning & decision making                   │
│  • Tool execution via MCP                       │
│  • Permission handling                          │
└─────────────────────┬───────────────────────────┘
                      │ Tool results + context
                      ▼
┌─────────────────────────────────────────────────┐
│         Direct Anthropic API (Call #2)          │
│  • Token-by-token streaming                     │
│  • Smooth UI updates                            │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│         Lifecycle Hooks (Memory)                 │
│  on_stop() → Extract & persist observations     │
└─────────────────────────────────────────────────┘
```

### 2. Streaming Agent

Direct Anthropic API with MCP tool support:

```
User Message → Direct API Stream → Token-by-token → UI
                    ↓
              Tool detected?
                    ↓
              MCP Client → Execute → Continue stream
```

### 3. Legacy Block Agent

Original Claude SDK-only approach (block-level responses).

## Memory System (Claude-Mem Inspired)

The agent features a persistent memory system with:

### Lifecycle Hooks

| Hook | When | Purpose |
|------|------|---------|
| `on_session_start` | Session begins | Initialize context |
| `on_prompt_submit` | Before processing | Inject relevant memories |
| `on_tool_use` | After tool execution | Track tool usage |
| `on_stop` | Response complete | Extract observations |
| `on_session_end` | Session terminates | Generate summary |

### Memory Components

```
┌─────────────────────────────────────────────────┐
│              Memory Architecture                 │
├─────────────────────────────────────────────────┤
│                                                  │
│  ┌──────────────┐    ┌──────────────────────┐   │
│  │ Observations │    │  Session Summaries   │   │
│  │  (granular)  │    │    (compressed)      │   │
│  └──────┬───────┘    └──────────┬───────────┘   │
│         │                       │               │
│         └───────────┬───────────┘               │
│                     │                           │
│         ┌───────────▼───────────┐               │
│         │    Hybrid Search      │               │
│         │  FTS + Vector + Score │               │
│         └───────────┬───────────┘               │
│                     │                           │
│         ┌───────────▼───────────┐               │
│         │ Progressive Disclosure│               │
│         │  (token-budgeted)     │               │
│         └───────────────────────┘               │
│                                                  │
└─────────────────────────────────────────────────┘
```

### Database Tables

| Table | Purpose |
|-------|---------|
| `claude_observations` | Granular facts/preferences with FTS |
| `claude_session_summaries` | Compressed session summaries |
| `claude_conversations` | Conversation metadata |
| `claude_messages` | Individual messages |
| `claude_memory` | User CLAUDE.md content |

## Endpoints

| Endpoint | Type | Description |
|----------|------|-------------|
| `/ws/chat` | WebSocket | Main chat with memory support |
| `/ws/stream` | WebSocket | Token-level streaming chat |
| `/ws/secure/chat` | WebSocket | Zero-trust secured chat |
| `/api/conversations` | REST | Conversation history CRUD |
| `/api/memory` | REST | CLAUDE.md memory management |
| `/api/mcp/*` | REST | MCP server management |
| `/health` | REST | Health check |

## Tech Stack

- **Framework**: FastAPI + Uvicorn
- **AI**: Claude SDK, Anthropic API (streaming)
- **Protocol**: MCP (Model Context Protocol)
- **Database**: PostgreSQL (via Supabase)
- **Search**: PostgreSQL FTS + ChromaDB (optional)
- **Auth**: JWT tokens, Zero-Trust certificates
- **Cache**: Redis (rate limiting)

## Quick Start

### Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ANTHROPIC_API_KEY=your-key
export DATABASE_URL=postgresql://...
export SUPABASE_URL=https://...
export SUPABASE_SERVICE_ROLE_KEY=...

# Run migrations (for memory system)
# Apply: supabase/migrations/20260116000000_create_memory_system.sql

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `SUPABASE_URL` | Supabase instance URL | Required |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service key | Required |
| `SUPABASE_JWT_SECRET` | JWT verification secret | - |
| `REDIS_URL` | Redis URL for rate limiting | - |
| `PORT` | Server port | `8002` |
| `USER_WORKSPACES_DIR` | User workspace directory | `./workspaces` |
| `AI_ALLOWED_ORIGINS` | CORS allowed origins | `localhost:3000` |

### Docker

```bash
# Build image
docker build -t inres-agent .

# Run container
docker run -p 8002:8002 \
  -e ANTHROPIC_API_KEY=your-key \
  -e DATABASE_URL=postgresql://... \
  -e SUPABASE_URL=https://... \
  -e SUPABASE_SERVICE_ROLE_KEY=... \
  inres-agent
```

## Features

### Core Features
- **Incident Tools**: Create, update, and manage incidents via natural language
- **MCP Integration**: Connect to external tools via Model Context Protocol
- **Token Streaming**: Real-time token-level response streaming
- **Hybrid Architecture**: SDK for planning, Direct API for streaming

### Memory Features (New)
- **Persistent Memory**: Observations and session summaries
- **Progressive Disclosure**: Token-budgeted context injection
- **Hybrid Search**: FTS + vector similarity (optional ChromaDB)
- **Session Summarization**: AI-powered session summaries
- **Lifecycle Hooks**: Claude-mem style hook system

### Security Features
- **Zero-Trust Security**: Device certificate verification
- **Audit Logging**: Security audit trail for all agent actions
- **Rate Limiting**: Redis-backed with backpressure handling
- **JWT Auth**: Supabase JWT token verification

### Extensibility
- **Plugin Marketplace**: Extensible plugin system
- **Custom Tools**: Add tools via MCP or direct integration
- **Agent Factory**: Runtime agent switching

## Development

### Agent Implementations

| Agent | File | Use Case |
|-------|------|----------|
| `SDKHybridAgent` | `hybrid/sdk_agent.py` | Production (recommended) |
| `StreamingAgent` | `streaming/agent.py` | Direct API streaming |
| Legacy Agent | `claude_agent.py` | Backward compatibility |

### Adding Memory Features

```python
from core.lifecycle_hooks import MemoryLifecycleHooks, SessionContext
from hybrid.sdk_agent import SDKHybridAgent, SDKHybridAgentConfig

# Configure agent with memory
config = SDKHybridAgentConfig(
    enable_memory=True,
    enable_context_injection=True,
    enable_observation_extraction=True,
    enable_session_summary=True,
    context_token_budget=2000,
)

agent = SDKHybridAgent(config=config)

# Start session
context = await agent.start_session(
    user_id="user-uuid",
    session_id="session-uuid",
)

# Process messages (memories auto-injected)
response = await agent.process_message(
    prompt="Show me recent incidents",
    output_queue=queue,
)

# End session (summary auto-generated)
await agent.end_session()
```

### Adding New Tools

Tools are defined in `tools/` and exposed via MCP:

```python
# tools/incidents.py
@mcp_server.tool()
async def create_incident(title: str, severity: str, ...):
    """Create a new incident."""
    # Implementation
```

### Optional: Enable Vector Search

```bash
# Install optional dependencies
pip install chromadb sentence-transformers

# Vector search will be automatically enabled
```

### Running Tests

```bash
pytest tests/
```

## Related Services

- **inres-api** (`:8080`) - Go backend API
- **inres-frontend** (`:3000`) - Next.js web UI
- **inres-slack-worker** - Slack integration worker

## Migration Notes

### Memory System Migration

Apply the memory system migration to enable observations and summaries:

```sql
-- Run this migration
supabase/migrations/20260116000000_create_memory_system.sql
```

This creates:
- `claude_observations` table with FTS
- `claude_session_summaries` table
- Helper functions for search
