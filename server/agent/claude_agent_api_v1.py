"""
Claude Agent API v1 - WebSocket chat endpoints.

Each connection gets one ``ChatSession`` (see the ``session`` package), which
holds a single Claude Agent SDK client open for the life of the conversation.
That one client handles planning, tools, MCP and token-level streaming, so the
conversation keeps its context between turns and the UI sees text as it is
generated.

Endpoints:
    /ws/chat         JWT-authenticated chat
    /ws/secure/chat  the same, with a zero-trust signed envelope per message
    /api/*           REST routers (conversations, audit, MCP, plugins, memory)

The two sockets share everything but authentication and frame unwrapping; the
common half lives in ``ws_chat.ChatConnection``.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

# Load config from YAML (unifies config with Go API)
from config import loader as config_loader
config_loader.load_config()

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Import from organized packages
from security import get_verifier, init_verifier
from audit import (
    get_audit_service,
    init_audit_service,
    shutdown_audit_service,
    EventType,
)
from services import (
    extract_user_id_from_token,
    get_user_mcp_servers,
    start_pgmq_consumer,
    stop_pgmq_consumer,
)

# Import routers from routes package
from routes import (
    db_router,
    conversations_router,
    audit_router,
    sync_router,
    mcp_router,
    tools_router,
    memory_router,
    marketplace_router,
)

from config import config
from errors import sanitize_error_message
from session import configure_concurrency, events
from streaming.mcp_client import MCPToolManager, get_mcp_pool
from ws_chat import ChatConnection, build_session_config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# sanitize_error_message now lives in errors.py so the session layer can use it
# too without importing this module.


# ==========================================
# Rate Limiting (Redis-backed for horizontal scaling)
# ==========================================

from utils.redis_client import get_rate_limiter, get_session_store, close_redis

# Get rate limit config from environment
RATE_LIMIT_REQUESTS = int(os.getenv("AI_RATE_LIMIT", "60"))
RATE_LIMIT_WINDOW = 60  # seconds


async def check_rate_limit(user_id: str) -> bool:
    """
    Check if user has exceeded rate limit using Redis.

    Args:
        user_id: User identifier

    Returns:
        True if within rate limit, False if exceeded
    """
    rate_limiter = get_rate_limiter()
    return await rate_limiter.is_allowed(user_id)


async def rate_limit_middleware(request: Request, call_next):
    """
    Rate limiting middleware for all API endpoints.

    Uses Redis-backed rate limiting for horizontal scaling.
    Limits requests per user based on AI_RATE_LIMIT environment variable.
    """
    # Skip rate limiting for health check
    if request.url.path == "/health":
        return await call_next(request)

    # Extract user_id from token
    auth_token = (
        request.query_params.get("auth_token")
        or request.headers.get("authorization", "")
    )

    if auth_token:
        user_id = extract_user_id_from_token(auth_token)
        if user_id:
            # Check rate limit using Redis
            if not await check_rate_limit(user_id):
                # Log rate limit event
                audit = get_audit_service()
                await audit.log_security_event(
                    event_type=EventType.AUTH_RATE_LIMITED,
                    user_id=user_id,
                    action="rate_limit_check",
                    error_code="RATE_LIMIT_EXCEEDED",
                    error_message=f"Exceeded {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s",
                    source_ip=request.client.host if request.client else None,
                    metadata={"path": str(request.url.path)}
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "success": False,
                        "error": "Rate limit exceeded. Please try again later.",
                        "retry_after": RATE_LIMIT_WINDOW,
                    },
                )

    return await call_next(request)


async def _sweep_audit_contexts(interval_s: int = 600) -> None:
    """Periodically drop audit contexts whose PostToolUse hook never fired."""
    from audit.hooks import cleanup_stale_contexts

    try:
        while True:
            await asyncio.sleep(interval_s)
            try:
                cleanup_stale_contexts()
            except Exception:
                logger.exception("Audit context sweep failed")
    except asyncio.CancelledError:
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI.
    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting application...")

    # Initialize audit service
    await init_audit_service()
    logger.info("📝 Audit service initialized")

    # Start PGMQ consumer for incident analytics
    await start_pgmq_consumer()
    logger.info("🤖 Incident analytics PGMQ consumer started")

    # Cap live CLI subprocesses: one per active chat session, so this is what
    # keeps many open tabs from exhausting the container.
    configure_concurrency(config.agent.max_concurrent_cli)

    # Audit hooks record a context per tool call and drop it on PostToolUse,
    # which never fires for denied or interrupted tools. Sweep the leftovers.
    audit_sweeper = asyncio.create_task(_sweep_audit_contexts(), name="audit-sweeper")

    # heartbeat is per-connection; marketplace cleanup is synchronous.

    logger.info("Application started")

    yield

    # Shutdown
    logger.info("🛑 Stopping application...")

    audit_sweeper.cancel()

    # Stop PGMQ consumer
    await stop_pgmq_consumer()
    logger.info("🤖 Incident analytics PGMQ consumer stopped")

    # Close Redis connection
    await close_redis()
    logger.info("🔴 Redis connection closed")

    # Shutdown audit service (flush remaining events)
    await shutdown_audit_service()
    logger.info("📝 Audit service stopped")

    logger.info("Application stopped")


app = FastAPI(
    title="Claude Agent API",
    description="WebSocket API for Claude Agent SDK with session management",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware - Configure allowed origins from environment
# For development: use specific localhost domains
# For production: MUST use specific domains only (never use "*")
# SECURITY: Using "*" with allow_credentials=True is a security vulnerability
ALLOWED_ORIGINS = os.getenv(
    "AI_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:8000"
).split(",")

# Strip whitespace from origins
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]

logger.info(f"CORS configured with allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Apply rate limiting middleware
app.middleware("http")(rate_limit_middleware)
logger.info(f"[Rate Limiting]Enabled: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds")

# Include database routes (installed_plugins, marketplaces)
app.include_router(db_router)
logger.info("[Database] Database routes loaded from routes_db.py")

# Include conversation history routes
app.include_router(conversations_router)
logger.info("[Conversation] Conversation routes loaded from routes_conversations.py")

# Include audit routes
app.include_router(audit_router)
logger.info("[Audit] Audit routes loaded from routes_audit.py")

# Include modular routes
app.include_router(sync_router)
logger.info("[Sync] Sync routes loaded from routes_sync.py")

app.include_router(mcp_router)
logger.info("[MCP] MCP routes loaded from routes_mcp.py")

app.include_router(tools_router)
logger.info("[Tools] Tools routes loaded from routes_tools.py")

app.include_router(memory_router)
logger.info("[Memory] Memory routes loaded from routes_memory.py")

app.include_router(marketplace_router)
logger.info("[Marketplace] Marketplace routes loaded from routes_marketplace.py")

logger.info("[Agent] Persistent SDK sessions serve /ws/chat and /ws/secure/chat")

# The incident tools MCP server registered with every session.
BUILTIN_TOOL_COUNT = 5


async def verify_websocket_auth(websocket: WebSocket) -> tuple[bool, str]:
    """
    Verify WebSocket authentication before accepting connection.

    Returns:
        tuple: (is_valid, user_id or error_message)
    """
    # Get token from query parameters
    token = websocket.query_params.get("token")

    if not token:
        logger.warning("WebSocket connection attempt without token")
        return False, "Missing authentication token"

    try:
        # Verify JWT token
        user_id = extract_user_id_from_token(token)
        if not user_id:
            logger.warning("WebSocket connection attempt with invalid token")
            return False, "Invalid authentication token"

        logger.info(f"  WebSocket authenticated for user: {user_id}")
        return True, user_id

    except Exception as e:
        logger.error(f"WebSocket auth error: {e}")
        return False, "Authentication failed"


async def _load_mcp_servers(user_id: str, auth_token: str):
    """
    Bring up the user's configured MCP servers for this connection.

    Never fatal: a chat with only the built-in incident tools is far better
    than a refused connection, so failures fall back to an empty manager.
    """
    try:
        logger.info(f"Loading MCP servers for user: {user_id}")
        user_mcp_config = await get_user_mcp_servers(auth_token=auth_token, user_id=user_id)

        if not user_mcp_config:
            logger.info("No MCP servers configured")
            return MCPToolManager(), []

        logger.info(f"Found {len(user_mcp_config)} MCP server configs")
        pool = await get_mcp_pool()
        manager = await pool.get_servers_for_user(user_id, user_mcp_config)
        tools = manager.get_all_tools()
        logger.info(f"Loaded {len(tools)} MCP tools")
        return manager, tools

    except Exception as e:
        logger.error(f"Failed to load MCP servers: {e}", exc_info=True)
        return MCPToolManager(), []


def _mcp_server_configs(manager) -> Dict[str, Any]:
    """External MCP server configs for the SDK, or {} if there are none."""
    if manager and manager.server_count > 0:
        return manager.get_server_configs()
    return {}


async def _release_mcp_servers(user_id: str) -> None:
    try:
        pool = await get_mcp_pool()
        await pool.release_servers_for_user(user_id)
    except Exception as e:
        logger.error(f"Failed to release MCP servers: {e}")


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    Chat over a persistent Claude Agent SDK session.

    Protocol:
        connect  ?token=JWT&org_id=...&project_id=...
        ->       {"type": "session_created", "session_id", "conversation_id", ...}
        send     {"prompt": "...", "conversation_id": "..."}
        <-       delta / thinking / tool_use / tool_result frames
        <-       exactly one of complete | error | interrupted

    Also accepts {"type": "interrupt"}, {"type": "clear_history"},
    {"type": "permission_response", "request_id", "allow"} and {"type": "pong"}.

    The receive loop never awaits the agent. Turns run in the session's own
    task, so a message can always be read - which is what lets an approval or
    an interrupt reach a turn that is currently blocked.
    """
    audit = get_audit_service()
    client_ip = websocket.client.host if websocket.client else None

    ws_org_id = websocket.query_params.get("org_id") or None
    ws_project_id = websocket.query_params.get("project_id") or None
    token = websocket.query_params.get("token") or ""
    requested_conversation = websocket.query_params.get("conversation_id") or None

    is_valid, result = await verify_websocket_auth(websocket)
    if not is_valid:
        await audit.log_auth_failed(
            user_id=None,
            error_code="WS_AUTH_FAILED",
            error_message=result,
            source_ip=client_ip,
        )
        await websocket.close(code=4001, reason="Unauthorized")
        return

    user_id = result
    await websocket.accept()

    session_id = str(uuid.uuid4())
    connection = None
    mcp_manager = None

    try:
        mcp_manager, mcp_tools = await _load_mcp_servers(user_id, auth_token=token)

        cfg = await build_session_config(
            user_id=user_id,
            session_id=session_id,
            auth_token=token,
            org_id=ws_org_id,
            project_id=ws_project_id,
            mcp_servers=_mcp_server_configs(mcp_manager),
        )

        connection = ChatConnection(
            websocket=websocket,
            cfg=cfg,
            user_id=user_id,
            session_id=session_id,
            conversation_id=session_id,
            mode="persistent_session",
        )

        if requested_conversation:
            await connection.resume_previous(requested_conversation)

        await audit.log_session_created(
            user_id=user_id,
            session_id=session_id,
            source_ip=client_ip,
            user_agent=websocket.headers.get("user-agent"),
            org_id=ws_org_id,
            project_id=ws_project_id,
            metadata={
                "mcp_servers": mcp_manager.server_count if mcp_manager else 0,
                "tools": len(mcp_tools) + BUILTIN_TOOL_COUNT,
                "model": cfg.model,
            },
        )

        # Sent before the connection's sender task exists, so this is the only
        # writer on the socket at this point. Everything afterwards goes through
        # the queue - two writers would interleave frames.
        await websocket.send_json({
            "type": "session_created",
            "session_id": session_id,
            "conversation_id": connection.conversation_id,
            "agent_type": "persistent_session",
            "model": cfg.model,
            "available_models": config.agent.available_models,
            "message": "Connected to inres AI agent",
            "mcp_servers": mcp_manager.server_count if mcp_manager else 0,
            "total_tools": len(mcp_tools) + BUILTIN_TOOL_COUNT,
        })
        logger.info(f"📤 Sent session_created: {session_id}")

        await connection.start()

        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                connection.emit(events.error("Invalid JSON message"))
                continue
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected: {session_id}")
                break

            msg_type = message.get("type", "chat")

            if msg_type == "pong":
                continue

            if msg_type == "interrupt":
                await connection.handle_interrupt()
                continue

            if msg_type == "clear_history":
                await connection.handle_clear_history()
                continue

            if msg_type == "permission_response":
                connection.handle_permission_response(
                    message.get("request_id"), message.get("allow")
                )
                continue

            if msg_type == "set_model":
                await connection.handle_set_model(message.get("model"))
                continue

            prompt = message.get("prompt", "")
            msg_org_id = message.get("org_id") or ws_org_id
            msg_project_id = message.get("project_id") or ws_project_id

            if prompt:
                await audit.log_chat_message(
                    user_id=user_id,
                    session_id=session_id,
                    conversation_id=message.get("conversation_id") or connection.conversation_id,
                    message_preview=prompt[:100],
                    org_id=msg_org_id,
                    project_id=msg_project_id,
                )

            await connection.handle_chat(
                prompt=prompt,
                org_id=msg_org_id,
                project_id=msg_project_id,
                conversation_id=message.get("conversation_id"),
            )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        if connection:
            connection.emit(events.error(sanitize_error_message(e, "in WebSocket")))
    finally:
        logger.info(f"Cleaning up session: {session_id}")
        if connection:
            await connection.aclose()
        await _release_mcp_servers(user_id)
        logger.info(f"Session cleanup complete: {session_id}")


@app.websocket("/ws/secure/chat")
async def websocket_secure_chat(websocket: WebSocket):
    """
    Zero-trust chat: the same session machinery as /ws/chat, with every message
    cryptographically signed by the client device and verified here.

    Handshake:
    1. Client sends a signed authenticate message carrying its device certificate
    2. Server checks the certificate was issued by a trusted instance
    3. Every later message is signed and verified against the device public key

    Frames carry their real type inside ``payload.type`` (note ``chat_message``
    rather than the plain socket's bare prompt).
    """
    await websocket.accept()

    audit = get_audit_service()
    client_ip = websocket.client.host if websocket.client else None
    verifier = get_verifier()

    ws_org_id = websocket.query_params.get("org_id") or None
    ws_project_id = websocket.query_params.get("project_id") or None
    logger.info(f"Secure WebSocket params - org_id: {ws_org_id}, project_id: {ws_project_id}")

    session = None
    session_id = None
    user_id = None
    connection = None
    mcp_manager = None

    try:
        logger.info("Waiting for Zero-Trust authentication...")
        auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)

        if auth_data.get("type") != "authenticate":
            await audit.log_auth_failed(
                user_id=None,
                error_code="INVALID_AUTH_TYPE",
                error_message="Expected authentication message",
                source_ip=client_ip,
            )
            await websocket.send_json({"type": "auth_error", "error": "Expected authentication message"})
            await websocket.close(code=4001)
            return

        cert_dict = auth_data.get("certificate")
        existing_session_id = auth_data.get("session_id")

        if not cert_dict:
            await audit.log_auth_failed(
                user_id=None,
                error_code="MISSING_CERTIFICATE",
                error_message="Missing device certificate",
                source_ip=client_ip,
            )
            await websocket.send_json({"type": "auth_error", "error": "Missing device certificate"})
            await websocket.close(code=4002)
            return

        session, error = await verifier.authenticate(cert_dict, existing_session_id)

        if not session:
            logger.warning(f"🚫 Zero-Trust authentication failed: {error}")
            error_code = "AUTH_FAILED"
            if "expired" in error.lower():
                error_code = "CERTIFICATE_EXPIRED"
            elif "invalid" in error.lower():
                error_code = "INVALID_CERTIFICATE"
            await audit.log_auth_failed(
                user_id=cert_dict.get("user_id"),
                error_code=error_code,
                error_message=error,
                source_ip=client_ip,
                metadata={"instance_id": cert_dict.get("instance_id")},
            )
            await websocket.send_json({"type": "auth_error", "error": error})
            await websocket.close(code=4003)
            return

        session_id = session.session_id
        user_id = session.user_id
        logger.info(f"Zero-Trust authenticated: user={user_id}, session={session_id}")

        await audit.log_session_authenticated(
            user_id=user_id,
            session_id=session_id,
            device_cert_id=cert_dict.get("id", ""),
            instance_id=cert_dict.get("instance_id", ""),
            source_ip=client_ip,
            org_id=ws_org_id,
            project_id=ws_project_id,
            metadata={"permissions": session.permissions},
        )

        mcp_manager, mcp_tools = await _load_mcp_servers(user_id, auth_token="")

        # Zero-trust clients authenticate by device certificate, so there is no
        # bearer token to hand the tools.
        cfg = await build_session_config(
            user_id=user_id,
            session_id=session_id,
            auth_token="",
            org_id=ws_org_id,
            project_id=ws_project_id,
            mcp_servers=_mcp_server_configs(mcp_manager),
        )

        connection = ChatConnection(
            websocket=websocket,
            cfg=cfg,
            user_id=user_id,
            session_id=session_id,
            conversation_id=session_id,
            mode="persistent_session-secure",
        )

        # Sent before the sender task starts, so there is only one writer here.
        await websocket.send_json({
            "type": "authenticated",
            "session_id": session_id,
            "conversation_id": connection.conversation_id,
            "user_id": user_id,
            "permissions": session.permissions,
            "agent_type": "persistent_session",
            "model": cfg.model,
            "available_models": config.agent.available_models,
            "mcp_servers": mcp_manager.server_count if mcp_manager else 0,
            "total_tools": len(mcp_tools) + BUILTIN_TOOL_COUNT,
        })

        await connection.start()

        while True:
            try:
                signed_message = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info(f"Secure WebSocket disconnected: {session_id}")
                break

            if signed_message.get("type") == "pong":
                continue

            is_valid, error_msg, data = verifier.verify_message(signed_message, session_id)

            if not is_valid:
                logger.warning(f"🚫 Message verification failed: {error_msg}")
                error_type = EventType.SIGNATURE_INVALID
                if "nonce" in error_msg.lower() or "replay" in error_msg.lower():
                    error_type = EventType.NONCE_REPLAY
                await audit.log_security_event(
                    event_type=error_type,
                    user_id=user_id,
                    action="verify_message",
                    error_code="VERIFICATION_FAILED",
                    error_message=error_msg,
                    source_ip=client_ip,
                    session_id=session_id,
                )
                connection.emit(events.error(f"Message verification failed: {error_msg}"))
                continue

            msg_type = signed_message.get("payload", {}).get("type", "")

            if msg_type == "interrupt":
                await connection.handle_interrupt()
                continue

            if msg_type == "clear_history":
                await connection.handle_clear_history()
                continue

            if msg_type == "permission_response":
                connection.handle_permission_response(data.get("request_id"), data.get("allow"))
                continue

            if msg_type == "set_model":
                await connection.handle_set_model(data.get("model"))
                continue

            if msg_type != "chat_message":
                continue

            prompt = data.get("prompt", "")
            msg_org_id = data.get("org_id") or ws_org_id
            msg_project_id = data.get("project_id") or ws_project_id

            if prompt:
                await audit.log_chat_message(
                    user_id=user_id,
                    session_id=session_id,
                    conversation_id=data.get("conversation_id") or connection.conversation_id,
                    message_preview=prompt[:100],
                    org_id=msg_org_id,
                    project_id=msg_project_id,
                )

            await connection.handle_chat(
                prompt=prompt,
                org_id=msg_org_id,
                project_id=msg_project_id,
                conversation_id=data.get("conversation_id"),
            )

    except asyncio.TimeoutError:
        logger.warning("⏰ Zero-Trust authentication timeout")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "auth_error", "error": "Authentication timeout"})
    except WebSocketDisconnect:
        logger.info("🔌 Secure WebSocket disconnected")
    except Exception as e:
        logger.error(f"Secure WebSocket error: {e}", exc_info=True)
        if connection:
            connection.emit(events.error(sanitize_error_message(e, "in secure WebSocket")))
    finally:
        if session_id:
            logger.info(f"Session {session_id} kept for potential reconnection")
        if connection:
            await connection.aclose()
        if user_id:
            await _release_mcp_servers(user_id)
        logger.info("🧹 Secure WebSocket cleanup complete")


if __name__ == "__main__":
    import os

    import uvicorn

    # Initialize Zero-Trust verifier with backend URL
    backend_url = os.getenv("inres_BACKEND_URL", "")
    if backend_url:
        init_verifier(backend_url)
        logger.info(f"  Zero-Trust verifier initialized with backend: {backend_url}")
    else:
        logger.warning("inres_BACKEND_URL not set, Zero-Trust features limited")

    # Disable auto-reload in production to prevent sync issues
    # Auto-reload can cause server restarts during file operations (like sync)
    # which leads to background tasks hanging
    reload_enabled = os.getenv("DEV_MODE", "false").lower() == "true"

    uvicorn.run(
        "claude_agent_api_v1:app", host="0.0.0.0", port=8002, reload=reload_enabled
    )
