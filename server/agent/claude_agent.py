"""
Claude Agent API v1 - Production Hybrid Agent.

This module provides the main WebSocket API using the HybridAgent that combines:
- SDK-style orchestration for planning and tool management
- Token-level streaming for smooth UI experience
- Full MCP server support

The hybrid approach provides the best of both worlds:
- Fast token-by-token streaming (like direct API)
- Smart tool orchestration (like Claude Agent SDK)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

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
    get_user_allowed_tools,
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
    save_conversation,
    save_message,
    update_conversation_activity,
    promote_placeholder_conversation_preview,
    generate_new_chat_display_fields,
    verify_conversation_owner,
    load_agent_messages_for_resume,
)

# Import SDK Hybrid Agent (production agent with Claude Agent SDK)
from hybrid import SDKHybridAgent, SDKHybridAgentConfig
from streaming.mcp_client import MCPToolManager, get_mcp_pool
from core.tool_approval import ToolApprovalPolicy, ToolApprovalSession

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==========================================
# Async Utilities for Non-Blocking Operations
# ==========================================

# Queue configuration for backpressure management
OUTPUT_QUEUE_MAX_SIZE = 500  # Prevent unbounded memory growth
SEND_TIMEOUT_SECONDS = 5.0  # Timeout for slow clients


def fire_and_forget(coro) -> asyncio.Task:
    """
    Schedule a coroutine to run without blocking the caller.
    
    Used for non-critical operations like persistence that shouldn't
    block the AI streaming response.
    
    Args:
        coro: Coroutine to execute in background
        
    Returns:
        Task handle (for tracking, usually ignored)
    """
    task = asyncio.create_task(coro)
    
    def _handle_exception(t: asyncio.Task):
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.error(f"Fire-and-forget task failed: {exc}", exc_info=exc)
    
    task.add_done_callback(_handle_exception)
    return task


def schedule_save_ws_tool_event(conversation_id: Optional[str], event: Any) -> None:
    """
    Persist tool_use / tool_result rows so conversation resume/history matches live WebSocket UX.
    Scheduled before send so traces survive slow clients that drop queued events.
    """
    if not conversation_id or not isinstance(event, dict):
        return
    et = event.get("type")
    if et == "tool_use":
        name = (event.get("name") or "").strip() or None
        inp = event.get("input")
        if not isinstance(inp, dict):
            inp = {}
        payload = json.dumps(
            {"id": event.get("id"), "name": event.get("name"), "input": inp},
            default=str,
        )
        fire_and_forget(
            save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=payload,
                message_type="tool_use",
                tool_name=name,
                tool_input=inp or None,
            )
        )
    elif et == "tool_result":
        raw = event.get("content")
        if raw is None:
            text_out = ""
        elif isinstance(raw, str):
            text_out = raw
        else:
            text_out = json.dumps(raw, default=str)
        meta: Dict[str, Any] = {
            "tool_use_id": event.get("tool_use_id"),
            "is_error": bool(event.get("is_error")),
        }
        fire_and_forget(
            save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=text_out,
                message_type="tool_result",
                metadata=meta,
            )
        )


def sanitize_error_message(error: Exception, context: str = "") -> str:
    """
    Sanitize error messages to prevent information disclosure.

    Returns a generic error message while logging full details.

    Args:
        error: The exception to sanitize
        context: Context string for logging (e.g., "syncing bucket", "creating session")

    Returns:
        Generic error message safe to return to client
    """
    # Log full error details for debugging
    logger.error(f"Error {context}: {type(error).__name__}: {str(error)}", exc_info=True)

    # Return generic message based on error type
    if isinstance(error, (ConnectionError, TimeoutError)):
        return "Service temporarily unavailable. Please try again."
    elif isinstance(error, PermissionError):
        return "Access denied. Please check your permissions."
    elif isinstance(error, ValueError):
        return "Invalid input provided. Please check your request."
    elif "auth" in str(error).lower() or "token" in str(error).lower():
        return "Authentication failed. Please verify your credentials."
    elif "database" in str(error).lower() or "postgres" in str(error).lower():
        return "Database error. Please contact support if this persists."
    else:
        return "An internal error occurred. Please contact support if this persists."


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

    # Extract user_id from token (query or header only; JSON body is read in routes)
    try:
        auth_token = (
            request.query_params.get("auth_token")
            or request.headers.get("authorization", "")
        )

        if auth_token:
            user_id = extract_user_id_from_token(auth_token)
            if user_id:
                # Check rate limit using Redis
                if not await check_rate_limit(user_id):
                    # Log rate limit event (must not take down the request if audit fails)
                    try:
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
                    except Exception as audit_err:
                        logger.error(f"Rate limit audit log failed: {audit_err}", exc_info=True)

                    return JSONResponse(
                        status_code=429,
                        content={
                            "success": False,
                            "error": "Rate limit exceeded. Please try again later.",
                            "retry_after": RATE_LIMIT_WINDOW,
                        },
                    )
    except Exception as e:
        logger.error(f"Rate limit middleware error (fail-open): {e}", exc_info=True)

    return await call_next(request)


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
    logger.info("Audit service initialized")

    # Start PGMQ consumer for incident analytics
    await start_pgmq_consumer()
    logger.info("Incident analytics PGMQ consumer started")

    # No background workers needed anymore:
    # - heartbeat_task is per-connection (called in websocket endpoint)
    # - marketplace cleanup is now synchronous (no worker needed)

    logger.info("Application started")

    yield

    # Shutdown
    logger.info("Stopping application...")

    # Stop PGMQ consumer
    await stop_pgmq_consumer()
    logger.info("Incident analytics PGMQ consumer stopped")

    # Close Redis connection
    await close_redis()
    logger.info("Redis connection closed")

    # Shutdown audit service (flush remaining events)
    await shutdown_audit_service()
    logger.info("Audit service stopped")

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
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000",
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

# Hybrid agent is now the main /ws/chat endpoint
logger.info("[Hybrid] HybridAgent is the production agent (SDK orchestration + token streaming)")


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


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    Production WebSocket endpoint using HybridAgent.
    
    Combines:
    - SDK-style orchestration for smart tool planning
    - Token-level streaming for smooth UI experience
    - MCP server support for external integrations
    
    Protocol:
    1. Client connects with ?token=JWT&org_id=...&project_id=...
    2. Server authenticates, loads MCP servers, creates HybridAgent
    3. Client sends: {"prompt": "...", "session_id": "...", "conversation_id": "<optional>"}
       (omit conversation_id to keep the server's current thread; send id only to resume.)
    4. Client may send {"type": "new_conversation"} to start a new DB thread without reconnecting.
    5. Client may send {"type": "resume_conversation", "conversation_id": "..."} after loading history in the UI so the agent restores in-memory context.
    6. Server streams: {"type": "delta", "content": "token"}
    7. Server sends tool events during processing
    8. Server sends: {"type": "complete"} when done
    """
    audit = get_audit_service()
    client_ip = websocket.client.host if websocket.client else None

    # Extract params from query
    ws_org_id = websocket.query_params.get("org_id") or None
    ws_project_id = websocket.query_params.get("project_id") or None
    token = websocket.query_params.get("token") or ""
    logger.info(f"WebSocket params - org_id: {ws_org_id}, project_id: {ws_project_id}")

    # Authenticate BEFORE accepting connection (prevents DoS)
    is_valid, result = await verify_websocket_auth(websocket)
    if not is_valid:
        logger.warning(f"WebSocket auth failed: {result}")
        await audit.log_auth_failed(
            user_id=None,
            error_code="INVALID_TOKEN",
            error_message=result,
            source_ip=client_ip,
            org_id=ws_org_id
        )
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Accept connection - user is authenticated
    await websocket.accept()
    user_id = result
    session_id = str(uuid.uuid4())
    logger.info(f"WebSocket accepted for user: {user_id}")

    # Initialize MCP tool manager
    mcp_manager = None
    mcp_tools = []
    
    try:
        # Load user's MCP servers
        logger.info(f"Loading MCP servers for user: {user_id}")
        user_mcp_config = await get_user_mcp_servers(auth_token=token, user_id=user_id)
        
        if user_mcp_config:
            logger.info(f"Found {len(user_mcp_config)} MCP server configs")
            pool = await get_mcp_pool()
            mcp_manager = await pool.get_servers_for_user(user_id, user_mcp_config)
            mcp_tools = mcp_manager.get_all_tools()
            logger.info(f"Loaded {len(mcp_tools)} MCP tools")
        else:
            logger.info("No MCP servers configured")
            mcp_manager = MCPToolManager()
            
    except Exception as e:
        logger.error(f"Failed to load MCP servers: {e}", exc_info=True)
        mcp_manager = MCPToolManager()

    user_tool_patterns = await get_user_allowed_tools(user_id)
    approval_session = ToolApprovalSession()
    approval_policy = ToolApprovalPolicy(user_patterns=user_tool_patterns)

    config = SDKHybridAgentConfig(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        max_turns=10,
        system_prompt="""You are an AI assistant specialized in incident response and DevOps.
You help users manage incidents, analyze alerts, and troubleshoot issues.

## Built-in tools

**Incident management:**
- get_incidents_by_time, get_incident_by_id, get_incident_stats, get_current_time, search_incidents

**Release workflow (InRes API + local git only):**
- release_integration_guide, release_get_status, release_update_step, release_create_workflow,
  release_commit_and_push, release_record_pr, release_clone_and_branch, YAML/SOPS helpers

**External integrations (MCP):**
- Jira, Confluence, GitHub, ArgoCD, Coralogix, and other tools from MCP servers configured in InRes.

**Tool approvals:** Read-only incident lookups, release status/listing, and the integration guide run without a prompt. Mutating release steps, git operations, and every MCP tool require an in-app approval when the server sends a permission request (unless the tool matches your saved \"always allow\" list).

Be concise but thorough in your responses.""",
    )

    agent = SDKHybridAgent(
        config=config,
        mcp_manager=mcp_manager,
        approval_session=approval_session,
        approval_policy=approval_policy,
    )

    # Set auth context for built-in tools (incidents / release)
    agent.set_auth_context(
        auth_token=token,
        org_id=ws_org_id,
        project_id=ws_project_id,
    )

    estimated_tool_count = len(agent.tool_router.get_tool_schemas())
    
    # Log session created
    await audit.log_session_created(
        user_id=user_id,
        session_id=session_id,
        source_ip=client_ip,
        user_agent=websocket.headers.get("user-agent"),
        org_id=ws_org_id,
        project_id=ws_project_id
    )

    # Send session info to client
    await websocket.send_json({
        "type": "session_created",
        "session_id": session_id,
        "conversation_id": session_id,
        "agent_type": "inres_anthropic",
        "message": "InRes agent session established (Anthropic streaming + tools + MCP)",
        "mcp_servers": mcp_manager.server_count if mcp_manager else 0,
        "total_tools": estimated_tool_count
    })
    logger.info(f"Sent session_created: {session_id}")

    # Output queue for streaming events (bounded to prevent OOM)
    output_queue: asyncio.Queue = asyncio.Queue(maxsize=OUTPUT_QUEUE_MAX_SIZE)
    
    # Track session state
    is_first_message = True
    conversation_id = session_id
    stream_task = None
    sender_task = None
    heartbeat_task_ref = None
    client_healthy = True  # Track if client is responsive

    async def send_events():
        """
        Send events from queue to WebSocket with backpressure handling.
        
        If client is slow (send times out), we drain the queue to prevent
        memory buildup and mark the client as unhealthy.
        """
        nonlocal client_healthy
        try:
            while True:
                event = await output_queue.get()
                if event is None:
                    break
                try:
                    schedule_save_ws_tool_event(conversation_id, event)
                    await asyncio.wait_for(
                        websocket.send_json(event),
                        timeout=SEND_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # Client is too slow - drain queue to prevent memory issues
                    dropped_count = 0
                    while not output_queue.empty():
                        try:
                            output_queue.get_nowait()
                            dropped_count += 1
                        except asyncio.QueueEmpty:
                            break
                    logger.warning(
                        f"Client slow for session {session_id}, dropped {dropped_count} events"
                    )
                    client_healthy = False
                    # Send a warning to client that they're lagging
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": "Connection slow, some events dropped"
                        })
                    except Exception:
                        pass
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected during send")
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def heartbeat():
        """
        Send periodic pings directly to WebSocket (separate from token queue).
        
        This ensures heartbeats aren't blocked by a full token queue,
        allowing us to detect dead connections even under heavy streaming.
        """
        nonlocal client_healthy
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    # Send directly, bypass the token queue
                    await asyncio.wait_for(
                        websocket.send_json({"type": "ping", "timestamp": time.time()}),
                        timeout=SEND_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Heartbeat timeout for session {session_id}")
                    client_healthy = False
                except WebSocketDisconnect:
                    break
        except asyncio.CancelledError:
            pass

    try:
        sender_task = asyncio.create_task(send_events())
        heartbeat_task_ref = asyncio.create_task(heartbeat())
        
        while True:
            try:
                raw_message = await websocket.receive_text()
                message = json.loads(raw_message)
                
                msg_type = message.get("type", "chat")
                
                # Handle pong
                if msg_type == "pong":
                    continue
                
                # Handle interrupt
                if msg_type == "interrupt":
                    logger.info("Interrupt requested")
                    agent.interrupt()
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    await websocket.send_json({"type": "interrupted"})
                    continue

                if msg_type == "permission_response":
                    rid = message.get("request_id")
                    if rid is None:
                        await websocket.send_json(
                            {"type": "error", "error": "permission_response requires request_id"}
                        )
                        continue
                    allow = str(message.get("allow", "")).lower() in ("yes", "true", "1")
                    approval_session.resolve(str(rid), allow)
                    rp = message.get("remember_pattern")
                    if allow and isinstance(rp, str) and rp.strip():
                        approval_policy.add_runtime_pattern(rp.strip())
                    continue

                # Handle clear history
                if msg_type == "clear_history":
                    agent.clear_history()
                    await websocket.send_json({
                        "type": "history_cleared",
                        "message": "Conversation history cleared"
                    })
                    continue

                # New chat thread: same WebSocket session, new conversation_id for DB + fresh agent history
                if msg_type == "new_conversation":
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    agent.clear_history()
                    conversation_id = str(uuid.uuid4())
                    is_first_message = False
                    _nt, _fp = generate_new_chat_display_fields()
                    fire_and_forget(
                        save_conversation(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            first_message=_fp,
                            title=_nt,
                            model="claude-sonnet-4-sdk-hybrid",
                            metadata={
                                "org_id": ws_org_id,
                                "project_id": ws_project_id,
                                "mode": "sdk_hybrid",
                            },
                            initial_message_count=0,
                        )
                    )
                    await websocket.send_json({
                        "type": "conversation_started",
                        "conversation_id": conversation_id,
                    })
                    logger.info("Started new conversation thread: %s", conversation_id)
                    continue

                # Resume stored conversation into agent memory (same WebSocket)
                if msg_type == "resume_conversation":
                    rid = (message.get("conversation_id") or "").strip()
                    if not rid:
                        await websocket.send_json({
                            "type": "error",
                            "error": "conversation_id is required",
                        })
                        continue
                    if not verify_conversation_owner(rid, user_id):
                        await websocket.send_json({
                            "type": "error",
                            "error": "Conversation not found",
                        })
                        continue
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    msgs = load_agent_messages_for_resume(rid, user_id)
                    agent.set_history(msgs)
                    conversation_id = rid
                    is_first_message = False
                    await websocket.send_json({
                        "type": "conversation_resumed",
                        "conversation_id": rid,
                        "loaded_messages": len(msgs),
                    })
                    logger.info("Resumed conversation %s (%s messages)", rid, len(msgs))
                    continue
                
                # Handle chat message
                prompt = message.get("prompt", "")
                if not prompt:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Empty prompt"
                    })
                    continue

                # Update context if provided
                msg_org_id = message.get("org_id") or ws_org_id
                msg_project_id = message.get("project_id") or ws_project_id
                if msg_org_id or msg_project_id:
                    agent.set_auth_context(
                        auth_token=token,
                        org_id=msg_org_id,
                        project_id=msg_project_id
                    )
                
                # Resume / explicit thread: only accept non-empty client id (empty would fight new_conversation)
                cid = message.get("conversation_id")
                if cid:
                    conversation_id = cid
                
                logger.info(f"Processing: {prompt[:50]}...")
                
                # Fire-and-forget: Audit and persistence (non-blocking)
                # These operations shouldn't delay the AI response stream
                fire_and_forget(audit.log_chat_message(
                    user_id=user_id,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    message_preview=prompt[:100],
                    org_id=msg_org_id,
                    project_id=msg_project_id
                ))
                
                # Save conversation on first message (fire-and-forget)
                if is_first_message:
                    _nt, _ = generate_new_chat_display_fields()
                    fire_and_forget(save_conversation(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        first_message=prompt,
                        title=_nt,
                        model="claude-sonnet-4-sdk-hybrid",
                        metadata={
                            "org_id": msg_org_id,
                            "project_id": msg_project_id,
                            "mode": "sdk_hybrid"
                        }
                    ))
                    is_first_message = False
                
                # Save user message (fire-and-forget)
                fire_and_forget(save_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=prompt
                ))
                fire_and_forget(promote_placeholder_conversation_preview(conversation_id, prompt))
                
                # Cancel existing stream
                if stream_task and not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
                
                # Process with SDK hybrid agent
                async def process_and_save():
                    """Process with SDKHybridAgent and save response."""
                    response = await agent.process_message(
                        prompt=prompt,
                        output_queue=output_queue,
                        auth_token=token,
                        org_id=msg_org_id,
                        project_id=msg_project_id
                    )
                    
                    if response:
                        # Fire-and-forget: Save assistant response (non-blocking)
                        fire_and_forget(save_message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=response
                        ))
                        fire_and_forget(update_conversation_activity(conversation_id))
                    
                    return response
                
                stream_task = asyncio.create_task(process_and_save())
                
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "error": "Invalid JSON message"
                })
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected: {session_id}")
                break
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "error": sanitize_error_message(e, "in WebSocket")
            })
        except Exception:
            pass
    finally:
        # Cleanup
        logger.info(f"Cleaning up session: {session_id}")
        
        if stream_task and not stream_task.done():
            stream_task.cancel()
        if heartbeat_task_ref and not heartbeat_task_ref.done():
            heartbeat_task_ref.cancel()
        if sender_task and not sender_task.done():
            await output_queue.put(None)
            sender_task.cancel()
        
        # Release MCP servers
        try:
            pool = await get_mcp_pool()
            await pool.release_servers_for_user(user_id)
        except Exception as e:
            logger.error(f"Failed to release MCP servers: {e}")
        
        # Log session ended (fire-and-forget to not delay cleanup)
        fire_and_forget(audit.log_security_event(
            event_type=EventType.SESSION_ENDED,
            user_id=user_id,
            action="session_cleanup",
            session_id=session_id,
            source_ip=client_ip,
            org_id=ws_org_id,
            project_id=ws_project_id,
            metadata={"client_healthy": client_healthy}
        ))
        
        logger.info(f"Session cleanup complete: {session_id}")


@app.websocket("/ws/secure/chat")
async def websocket_secure_chat(websocket: WebSocket):
    """
    Zero-Trust Secure WebSocket for AI Agent using HybridAgent.

    Every message is cryptographically signed by the device and verified.
    This prevents session hijacking and replay attacks.

    Authentication flow:
    1. Client sends signed auth message with device certificate
    2. Server verifies certificate was signed by trusted instance
    3. Every subsequent message is signed by device's private key
    4. Server verifies each message against device's public key
    """
    await websocket.accept()

    audit = get_audit_service()
    client_ip = websocket.client.host if websocket.client else None
    verifier = get_verifier()

    # Extract org_id and project_id from query params
    ws_org_id = websocket.query_params.get("org_id") or None
    ws_project_id = websocket.query_params.get("project_id") or None
    logger.info(f"Secure WebSocket params - org_id: {ws_org_id}, project_id: {ws_project_id}")
    
    session = None
    session_id = None
    user_id = None
    
    # Agent and task references
    agent = None
    mcp_manager = None
    stream_task = None
    sender_task = None
    heartbeat_task_ref = None
    output_queue = asyncio.Queue(maxsize=OUTPUT_QUEUE_MAX_SIZE)
    client_healthy = True  # Track if client is responsive

    try:
        # Wait for authentication message
        logger.info("Waiting for Zero-Trust authentication...")
        auth_data = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=30.0
        )

        if auth_data.get("type") != "authenticate":
            await audit.log_auth_failed(
                user_id=None,
                error_code="INVALID_AUTH_TYPE",
                error_message="Expected authentication message",
                source_ip=client_ip
            )
            await websocket.send_json({
                "type": "auth_error",
                "error": "Expected authentication message"
            })
            await websocket.close(code=4001)
            return

        # Verify device certificate
        cert_dict = auth_data.get("certificate")
        existing_session_id = auth_data.get("session_id")

        if not cert_dict:
            await audit.log_auth_failed(
                user_id=None,
                error_code="MISSING_CERTIFICATE",
                error_message="Missing device certificate",
                source_ip=client_ip
            )
            await websocket.send_json({
                "type": "auth_error",
                "error": "Missing device certificate"
            })
            await websocket.close(code=4002)
            return

        # Authenticate with verifier
        session, error = await verifier.authenticate(cert_dict, existing_session_id)

        if not session:
            logger.warning(f"Zero-Trust authentication failed: {error}")
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
                metadata={"instance_id": cert_dict.get("instance_id")}
            )
            await websocket.send_json({
                "type": "auth_error",
                "error": error
            })
            await websocket.close(code=4003)
            return

        session_id = session.session_id
        user_id = session.user_id
        logger.info(f"Zero-Trust authenticated: user={user_id}, session={session_id}")

        # Log successful authentication
        await audit.log_session_authenticated(
            user_id=user_id,
            session_id=session_id,
            device_cert_id=cert_dict.get("id", ""),
            instance_id=cert_dict.get("instance_id", ""),
            source_ip=client_ip,
            org_id=ws_org_id,
            project_id=ws_project_id,
            metadata={"permissions": session.permissions}
        )

        # Initialize MCP tool manager
        mcp_tools = []
        try:
            logger.info(f"Loading MCP servers for user: {user_id}")
            user_mcp_config = await get_user_mcp_servers(auth_token="", user_id=user_id)
            
            if user_mcp_config:
                logger.info(f"Found {len(user_mcp_config)} MCP server configs")
                pool = await get_mcp_pool()
                mcp_manager = await pool.get_servers_for_user(user_id, user_mcp_config)
                mcp_tools = mcp_manager.get_all_tools()
                logger.info(f"Loaded {len(mcp_tools)} MCP tools")
            else:
                mcp_manager = MCPToolManager()
        except Exception as e:
            logger.error(f"Failed to load MCP servers: {e}")
            mcp_manager = MCPToolManager()

        user_tool_patterns = await get_user_allowed_tools(user_id)
        approval_session = ToolApprovalSession()
        approval_policy = ToolApprovalPolicy(user_patterns=user_tool_patterns)

        config = SDKHybridAgentConfig(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            max_turns=10,
            system_prompt="""You are an AI assistant specialized in incident response and DevOps.
You help users manage incidents, analyze alerts, and troubleshoot issues.
Be concise but thorough in your responses.""",
        )
        agent = SDKHybridAgent(
            config=config,
            mcp_manager=mcp_manager,
            approval_session=approval_session,
            approval_policy=approval_policy,
        )
        estimated_tool_count = len(agent.tool_router.get_tool_schemas())

        # Set auth context (Zero-Trust doesn't need token, uses device cert)
        agent.set_auth_context(
            auth_token="",  # Zero-Trust uses device cert instead
            org_id=ws_org_id,
            project_id=ws_project_id,
        )

        # Send auth success with session info
        await websocket.send_json({
            "type": "authenticated",
            "session_id": session_id,
            "conversation_id": session_id,
            "user_id": user_id,
            "permissions": session.permissions,
            "agent_type": "inres_anthropic",
            "mcp_servers": mcp_manager.server_count if mcp_manager else 0,
            "total_tools": estimated_tool_count
        })

        # Track session state
        is_first_message = True
        conversation_id = session_id

        async def send_events():
            """
            Send events from queue to WebSocket with backpressure handling.
            """
            nonlocal client_healthy
            try:
                while True:
                    event = await output_queue.get()
                    if event is None:
                        break
                    try:
                        schedule_save_ws_tool_event(conversation_id, event)
                        await asyncio.wait_for(
                            websocket.send_json(event),
                            timeout=SEND_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        # Client is too slow - drain queue
                        dropped_count = 0
                        while not output_queue.empty():
                            try:
                                output_queue.get_nowait()
                                dropped_count += 1
                            except asyncio.QueueEmpty:
                                break
                        logger.warning(
                            f"Secure client slow for session {session_id}, dropped {dropped_count} events"
                        )
                        client_healthy = False
                        try:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Connection slow, some events dropped"
                            })
                        except Exception:
                            pass
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.error(f"Send error: {e}")

        async def heartbeat():
            """
            Send periodic pings directly to WebSocket (separate from token queue).
            """
            nonlocal client_healthy
            try:
                while True:
                    await asyncio.sleep(30)
                    try:
                        await asyncio.wait_for(
                            websocket.send_json({"type": "ping", "timestamp": time.time()}),
                            timeout=SEND_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Secure heartbeat timeout for session {session_id}")
                        client_healthy = False
                    except WebSocketDisconnect:
                        break
            except asyncio.CancelledError:
                pass

        sender_task = asyncio.create_task(send_events())
        heartbeat_task_ref = asyncio.create_task(heartbeat())

        while True:
            try:
                signed_message = await websocket.receive_json()

                # Handle pong (not signed)
                if signed_message.get("type") == "pong":
                    continue

                # Verify signature on every message
                is_valid, error_msg, data = verifier.verify_message(
                    signed_message, session_id
                )

                if not is_valid:
                    logger.warning(f"Message verification failed: {error_msg}")
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
                        session_id=session_id
                    )
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Message verification failed: {error_msg}"
                    })
                    continue

                msg_type = signed_message.get("payload", {}).get("type", "")

                # Handle interrupt
                if msg_type == "interrupt":
                    logger.info("Interrupt requested")
                    agent.interrupt()
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    await websocket.send_json({"type": "interrupted"})
                    continue

                if msg_type == "permission_response":
                    rid = (data or {}).get("request_id")
                    if not rid:
                        await websocket.send_json(
                            {"type": "error", "error": "permission_response requires request_id in data"}
                        )
                        continue
                    allow = str((data or {}).get("allow", "")).lower() in ("yes", "true", "1")
                    approval_session.resolve(str(rid), allow)
                    rp = (data or {}).get("remember_pattern")
                    if allow and isinstance(rp, str) and rp.strip():
                        approval_policy.add_runtime_pattern(rp.strip())
                    continue

                # Handle clear history
                if msg_type == "clear_history":
                    agent.clear_history()
                    await websocket.send_json({
                        "type": "history_cleared",
                        "message": "Conversation history cleared"
                    })
                    continue

                if msg_type == "new_conversation":
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    agent.clear_history()
                    conversation_id = str(uuid.uuid4())
                    is_first_message = False
                    _nt, _fp = generate_new_chat_display_fields()
                    fire_and_forget(
                        save_conversation(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            first_message=_fp,
                            title=_nt,
                            model="claude-sonnet-4-sdk-hybrid",
                            metadata={
                                "org_id": ws_org_id,
                                "project_id": ws_project_id,
                                "mode": "sdk_hybrid-secure",
                            },
                            initial_message_count=0,
                        )
                    )
                    await websocket.send_json({
                        "type": "conversation_started",
                        "conversation_id": conversation_id,
                    })
                    logger.info("Secure WS: new conversation thread %s", conversation_id)
                    continue

                if msg_type == "resume_conversation":
                    rid = (data.get("conversation_id") or "").strip()
                    if not rid:
                        await websocket.send_json({"type": "error", "error": "conversation_id is required"})
                        continue
                    if not verify_conversation_owner(rid, user_id):
                        await websocket.send_json({"type": "error", "error": "Conversation not found"})
                        continue
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    msgs = load_agent_messages_for_resume(rid, user_id)
                    agent.set_history(msgs)
                    conversation_id = rid
                    is_first_message = False
                    await websocket.send_json({
                        "type": "conversation_resumed",
                        "conversation_id": rid,
                        "loaded_messages": len(msgs),
                    })
                    logger.info("Secure WS resumed %s (%s messages)", rid, len(msgs))
                    continue

                # Handle chat message
                if msg_type == "chat_message":
                    prompt = data.get("prompt", "")
                    if not prompt:
                        await websocket.send_json({
                            "type": "error",
                            "error": "Empty prompt"
                        })
                        continue

                    # Update context if provided
                    msg_org_id = data.get("org_id") or ws_org_id
                    msg_project_id = data.get("project_id") or ws_project_id
                    if msg_org_id or msg_project_id:
                        agent.set_auth_context(
                            auth_token="",  # Zero-Trust uses device cert
                            org_id=msg_org_id,
                            project_id=msg_project_id
                        )

                    cid = data.get("conversation_id")
                    if cid:
                        conversation_id = cid

                    logger.info(f"Processing: {prompt[:50]}...")

                    # Fire-and-forget: Audit and persistence (non-blocking)
                    fire_and_forget(audit.log_chat_message(
                        user_id=user_id,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        message_preview=prompt[:100],
                        org_id=msg_org_id,
                        project_id=msg_project_id
                    ))

                    # Save conversation on first message (fire-and-forget)
                    if is_first_message:
                        _nt, _ = generate_new_chat_display_fields()
                        fire_and_forget(save_conversation(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            first_message=prompt,
                            title=_nt,
                            model="claude-sonnet-4-sdk-hybrid",
                            metadata={
                                "org_id": msg_org_id,
                                "project_id": msg_project_id,
                                "mode": "sdk_hybrid-secure"
                            }
                        ))
                        is_first_message = False

                    # Save user message (fire-and-forget)
                    fire_and_forget(save_message(
                        conversation_id=conversation_id,
                        role="user",
                        content=prompt
                    ))
                    fire_and_forget(promote_placeholder_conversation_preview(conversation_id, prompt))

                    # Cancel existing stream
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass

                    # Process with SDK hybrid agent
                    async def process_and_save():
                        response = await agent.process_message(
                            prompt=prompt,
                            output_queue=output_queue,
                            auth_token="",  # Zero-Trust uses device cert
                            org_id=msg_org_id,
                            project_id=msg_project_id
                        )
                        if response:
                            # Fire-and-forget: Save assistant response (non-blocking)
                            fire_and_forget(save_message(
                                conversation_id=conversation_id,
                                role="assistant",
                                content=response
                            ))
                            fire_and_forget(update_conversation_activity(conversation_id))
                        return response

                    stream_task = asyncio.create_task(process_and_save())

            except WebSocketDisconnect:
                logger.info(f"Secure WebSocket disconnected: {session_id}")
                break

    except asyncio.TimeoutError:
        logger.warning("Zero-Trust authentication timeout")
        try:
            await websocket.send_json({
                "type": "auth_error",
                "error": "Authentication timeout"
            })
        except:
            pass
    except WebSocketDisconnect:
        logger.info("Secure WebSocket disconnected")
    except Exception as e:
        logger.error(f"Secure WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "error": sanitize_error_message(e, "in secure WebSocket")
            })
        except:
            pass
    finally:
        if session_id:
            logger.info(f"Session {session_id} kept for potential reconnection")

        # Cleanup
        if stream_task and not stream_task.done():
            stream_task.cancel()
        if heartbeat_task_ref and not heartbeat_task_ref.done():
            heartbeat_task_ref.cancel()
        if sender_task and not sender_task.done():
            await output_queue.put(None)
            sender_task.cancel()

        # Release MCP servers
        if user_id:
            try:
                pool = await get_mcp_pool()
                await pool.release_servers_for_user(user_id)
            except Exception as e:
                logger.error(f"Failed to release MCP servers: {e}")
            
            # Log session ended (fire-and-forget)
            fire_and_forget(audit.log_security_event(
                event_type=EventType.SESSION_ENDED,
                user_id=user_id,
                action="secure_session_cleanup",
                session_id=session_id,
                source_ip=client_ip,
                org_id=ws_org_id,
                project_id=ws_project_id,
                metadata={"client_healthy": client_healthy}
            ))

        logger.info("Secure WebSocket cleanup complete")


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
        "claude_agent:app", host="0.0.0.0", port=8002, reload=reload_enabled
    )
