"""
InRes AI Agent API - Main Entry Point.

This is the main FastAPI application using SDKHybridAgent that combines:
1. Claude Agent SDK for planning, tools, and MCP integration
2. Direct Anthropic API for token-level streaming

Architecture:
    main.py (this file)
    ├── /ws/chat        → SDKHybridAgent (SDK tools + token streaming)
    ├── /ws/secure/chat → SDKHybridAgent with Zero-Trust auth
    └── /api/*          → REST endpoints (routes/)

Data Flow:
    UI ◄── token stream ── Direct Anthropic API
                               ▲
                  Claude Agent SDK (planning / tools / MCP)
                               ▼
                         Business logic (InRes API)

Packages:
    - hybrid/       SDKHybridAgent + SDKOrchestrator (production agent)
    - tools/        @tool decorated functions for Claude Agent SDK
    - streaming/    MCP client pool
    - routes/       HTTP API endpoints
    - services/     Business logic (storage, analytics)
    - audit/        Security audit logging
    - security/     Zero trust verification
    - core/         Shared abstractions (BaseAgent)
    - config/       Configuration
    - utils/        Utilities

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8002 --reload
"""

from claude_agent import app

__all__ = ["app"]
