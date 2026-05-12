"""
Routes Package - All HTTP/WebSocket API Routes.

This package contains all FastAPI routers organized by feature:
- audit: Audit log endpoints
- conversations: Conversation history CRUD
- db: Database query endpoints
- marketplace: Plugin marketplace
- mcp: MCP server management
- memory: Memory/knowledge base
- sync: Data synchronization
- tools: Tool management

Usage:
    from routes import (
        audit_router,
        conversations_router,
        db_router,
        marketplace_router,
        mcp_router,
        memory_router,
        sync_router,
        tools_router,
    )
    
    app.include_router(audit_router)
"""

# Import routers with proper path handling
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .audit import router as audit_router
from .conversations import router as conversations_router
from .db import router as db_router
from .marketplace import router as marketplace_router
from .mcp import router as mcp_router
from .memory import router as memory_router
from .sync import router as sync_router
from .tools import router as tools_router

# Also export helper functions from conversations
from .conversations import (
    save_conversation,
    save_message,
    update_conversation_activity,
    promote_placeholder_conversation_preview,
    generate_new_chat_display_fields,
    verify_conversation_owner,
    load_agent_messages_for_resume,
)

__all__ = [
    # Routers
    "audit_router",
    "conversations_router", 
    "db_router",
    "marketplace_router",
    "mcp_router",
    "memory_router",
    "sync_router",
    "tools_router",
    # Helpers
    "save_conversation",
    "save_message",
    "update_conversation_activity",
    "promote_placeholder_conversation_preview",
    "generate_new_chat_display_fields",
    "verify_conversation_owner",
    "load_agent_messages_for_resume",
]
