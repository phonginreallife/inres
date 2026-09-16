"""
InRes AI Agent API - Main Entry Point.

FastAPI application serving chat over persistent Claude Agent SDK sessions.

Architecture:
    main.py (this file)
    ├── /ws/chat        → ChatSession (JWT auth)
    ├── /ws/secure/chat → ChatSession (zero-trust signed messages)
    └── /api/*          → REST endpoints (routes/)

Data flow: one Claude Agent SDK client stays connected for the life of a
WebSocket. It plans, runs tools (incident tools + the user's MCP servers) and
streams tokens in a single pass; the session layer translates its message
stream into WebSocket events.

    UI ◄── delta / tool events ── ChatSession ◄── Claude Agent SDK ──► tools
                                                                       │
                                                        Business logic (InRes API)

Packages:
    - session/      ChatSession, event translation, tool approval (the agent)
    - tools/        @tool decorated functions for Claude Agent SDK
    - streaming/    MCP client pool
    - routes/       HTTP API endpoints
    - services/     Business logic (storage, analytics)
    - audit/        Security audit logging
    - security/     Zero trust verification
    - config/       Configuration
    - utils/        Utilities

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8002 --reload
"""

from claude_agent_api_v1 import app

__all__ = ["app"]
