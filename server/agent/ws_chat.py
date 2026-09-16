"""
WebSocket chat plumbing shared by ``/ws/chat`` and ``/ws/secure/chat``.

The two endpoints differ only in how they authenticate and how they unwrap
incoming frames - one takes plain JSON, the other verifies a signed envelope.
Everything after that (session lifecycle, event fan-out, persistence, control
messages) is identical, and used to be duplicated, which is how their models and
system prompts drifted apart.

Task layout per connection::

    endpoint handler   reads frames, calls ChatConnection.handle_*
    sender task        drains the output queue to the socket
    heartbeat task     pings every 30s
    ChatSession runner owns the CLI subprocess and the turn loop
    persister tasks    one per turn; waits for the turn, then writes to the DB

Every outbound frame goes through the queue. Writing to the socket directly from
the handler as well would interleave frames with the sender task.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from config import config
from routes.conversations import (
    get_conversation_session,
    save_conversation,
    save_message,
    update_conversation_activity,
    update_conversation_session,
)
from session import ChatSession, SessionConfig, Turn, events

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 30


async def build_session_config(
    user_id: str,
    session_id: str,
    auth_token: Optional[str],
    org_id: Optional[str],
    project_id: Optional[str],
    mcp_servers: Optional[Dict[str, Any]] = None,
) -> SessionConfig:
    """Assemble a SessionConfig from global settings plus per-connection context."""
    agent_cfg = config.agent

    allowed_tools: List[str] = []
    try:
        from services.storage import get_user_allowed_tools

        allowed_tools = await get_user_allowed_tools(user_id) or []
    except Exception as exc:
        logger.warning("Could not load allowed tools for %s: %s", user_id, exc)

    hooks = None
    try:
        from audit import build_hooks_config

        hooks = build_hooks_config(user_id, session_id, org_id, project_id)
    except Exception as exc:
        logger.warning("Audit hooks unavailable: %s", exc)

    kwargs: Dict[str, Any] = {
        "user_id": user_id,
        "session_id": session_id,
        "auth_token": auth_token,
        "org_id": org_id,
        "project_id": project_id,
        "model": agent_cfg.model,
        "max_turns": agent_cfg.max_turns,
        "max_budget_usd": agent_cfg.max_budget_usd,
        "permission_mode": agent_cfg.permission_mode,
        "require_tool_approval": agent_cfg.require_tool_approval,
        "permission_timeout_s": agent_cfg.permission_timeout_s,
        "idle_timeout_s": agent_cfg.idle_timeout_s,
        "allowed_tools": allowed_tools,
        "external_mcp": mcp_servers or {},
        "hooks": hooks,
    }
    if agent_cfg.system_prompt:
        kwargs["system_prompt"] = agent_cfg.system_prompt

    return SessionConfig(**kwargs)


class ChatConnection:
    """Drives one WebSocket conversation."""

    def __init__(
        self,
        websocket: Any,
        cfg: SessionConfig,
        user_id: str,
        session_id: str,
        conversation_id: str,
        mode: str = "session",
    ) -> None:
        self.websocket = websocket
        self.cfg = cfg
        self.user_id = user_id
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.mode = mode

        # Unbounded on purpose. A bounded queue could block the permission
        # callback before its request was ever transmitted, and then nobody
        # could answer it.
        self.queue: "asyncio.Queue" = asyncio.Queue()
        self.session = ChatSession(cfg, self.queue)

        self._sender: Optional[asyncio.Task] = None
        self._heartbeat: Optional[asyncio.Task] = None
        self._persisters: List[asyncio.Task] = []
        self._is_first_message = True

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._sender = asyncio.create_task(self._send_events(), name="ws-sender")
        self._heartbeat = asyncio.create_task(self._heartbeat_loop(), name="ws-heartbeat")
        self.session.start()

    async def resume_previous(self, conversation_id: str) -> None:
        """
        Continue an earlier conversation, if this user owns it.

        Adopting the id and resuming the Claude session are separate decisions.
        A conversation whose first turn never produced a session id still has
        messages the user is looking at, so new turns must be appended to it -
        otherwise the transcript on screen and the rows being written drift
        apart. Only the model's context is lost, and that is recoverable.
        """
        claude_session_id = await get_conversation_session(self.user_id, conversation_id)

        if claude_session_id is None:
            logger.info(
                "Not resuming %s: no such conversation for user %s",
                conversation_id,
                self.user_id,
            )
            return

        self.conversation_id = conversation_id
        self._is_first_message = False

        if claude_session_id:
            self.session.seed_session_id(claude_session_id)
        else:
            logger.info(
                "Conversation %s has no Claude session recorded; continuing without context",
                conversation_id,
            )

    async def aclose(self) -> None:
        """
        Shut down in dependency order.

        Approvals are released first so the SDK is never left waiting on a
        decision that can no longer arrive; the session then disconnects
        cleanly instead of blocking until its own timeout.
        """
        if self.session.broker:
            self.session.broker.deny_all("Connection closed")

        with contextlib.suppress(Exception):
            await self.session.aclose()

        for task in self._persisters:
            if not task.done():
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)

        if self._heartbeat and not self._heartbeat.done():
            self._heartbeat.cancel()

        if self._sender and not self._sender.done():
            self.queue.put_nowait(None)  # flush, then stop
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._sender, timeout=2.0)
            self._sender.cancel()

    # -- outbound ----------------------------------------------------------

    def emit(self, event: Dict[str, Any]) -> None:
        """Queue a frame. Never blocks, never writes to the socket directly."""
        self.queue.put_nowait(event)

    async def _send_events(self) -> None:
        while True:
            event = await self.queue.get()
            if event is None:
                return
            try:
                await self.websocket.send_json(event)
            except Exception as exc:
                # One unserialisable frame must not silence the socket, so this
                # drops the frame and keeps going. Disconnects do end the loop.
                if _is_disconnect(exc):
                    logger.info("WebSocket closed while sending")
                    return
                logger.error("Dropping unsendable %s event: %s", event.get("type"), exc)

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                self.emit(events.ping(time.time()))
        except asyncio.CancelledError:
            return

    # -- control messages --------------------------------------------------

    async def handle_interrupt(self) -> None:
        logger.info("Interrupt requested for session %s", self.session_id)
        await self.session.interrupt()

    async def handle_clear_history(self) -> None:
        await self.session.reset()
        # A cleared conversation is a new row; reusing the old id would append
        # to history the user just asked to forget.
        self.conversation_id = str(uuid.uuid4())
        self._is_first_message = True
        self.emit(events.history_cleared(self.conversation_id))

    async def handle_set_model(self, model: Any) -> None:
        """
        Switch models mid-conversation.

        The requested model is checked against the configured allowlist before
        it goes anywhere near the CLI - this value arrives straight off a
        socket, and an unchecked string would let a caller choose any model the
        credential can reach.
        """
        agent_cfg = config.agent

        if not isinstance(model, str) or not agent_cfg.is_model_allowed(model):
            logger.warning("Rejected model switch to %r", model)
            self.emit(events.error(f"Model {model!r} is not available"))
            self.emit(events.model_changed(self.cfg.model, agent_cfg.available_models))
            return

        applied = await self.session.set_model(model)
        logger.info("Model set to %s (applied now: %s)", model, applied)
        self.emit(events.model_changed(model, agent_cfg.available_models, pending=not applied))

    def handle_permission_response(self, request_id: Optional[str], allow: Any) -> None:
        """
        Answer a pending tool approval.

        Synchronous by design: the receive loop holds the only key to a parked
        SDK callback and must not block while holding it.
        """
        broker = self.session.broker
        if broker is None:
            self.emit(events.error("Tool approval is not enabled for this session"))
            return

        approved = allow is True or (isinstance(allow, str) and allow.lower() in ("yes", "true", "allow"))
        if not broker.resolve(request_id, approved):
            self.emit(events.error("That approval request has expired"))

    async def handle_chat(
        self,
        prompt: str,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[Turn]:
        """Queue a user message. Returns immediately; the turn runs in the background."""
        if not prompt:
            self.emit(events.error("Empty prompt"))
            return None

        if conversation_id:
            self.conversation_id = conversation_id

        turn = Turn(
            prompt=prompt,
            conversation_id=self.conversation_id,
            auth_token=self.cfg.auth_token,
            org_id=org_id or self.cfg.org_id,
            project_id=project_id or self.cfg.project_id,
        )

        # Decide this synchronously so two messages in quick succession can't
        # both think they are the first and create the conversation twice.
        is_first = self._is_first_message
        self._is_first_message = False

        await self.session.submit(turn)

        # All database work happens here, off the receive loop, so the loop
        # stays free to read the next frame - an approval or an interrupt for
        # the turn that is currently running.
        persister = asyncio.create_task(self._persist_turn(turn, is_first), name="persist-turn")
        self._persisters.append(persister)
        self._persisters = [t for t in self._persisters if not t.done()]

        return turn

    # -- persistence -------------------------------------------------------

    async def _persist_turn(self, turn: Turn, is_first: bool) -> None:
        """
        Write the user message, then the assistant reply once the turn ends.

        Interrupted turns are stored too - the partial answer the user read is
        worth keeping. An empty reply is skipped: a blank row renders as an
        empty bubble and inflates message_count.
        """
        try:
            if is_first:
                await save_conversation(
                    user_id=self.user_id,
                    conversation_id=turn.conversation_id,
                    first_message=turn.prompt,
                    model=self.cfg.model,
                    metadata={
                        "org_id": turn.org_id,
                        "project_id": turn.project_id,
                        "mode": self.mode,
                    },
                )

            await save_message(
                conversation_id=turn.conversation_id,
                role="user",
                content=turn.prompt,
            )

            await turn.done.wait()

            if turn.text:
                await save_message(
                    conversation_id=turn.conversation_id,
                    role="assistant",
                    content=turn.text,
                    metadata={
                        "interrupted": turn.interrupted,
                        "cost_usd": turn.cost_usd,
                        "claude_session_id": turn.session_id,
                    },
                )
                await update_conversation_activity(turn.conversation_id)

            if turn.session_id:
                await update_conversation_session(turn.conversation_id, turn.session_id)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to persist turn for conversation %s", turn.conversation_id)


def _is_disconnect(exc: Exception) -> bool:
    """
    Whether a send failure means the socket is gone rather than the frame bad.

    Starlette signals a closed socket with WebSocketDisconnect, or with a
    RuntimeError complaining about sending after close.
    """
    if "Disconnect" in type(exc).__name__:
        return True
    if isinstance(exc, RuntimeError):
        text = str(exc).lower()
        return "close" in text or "disconnect" in text or "websocket is not connected" in text
    return False
