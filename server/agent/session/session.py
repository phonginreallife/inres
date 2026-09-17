"""
ChatSession - one persistent Claude session per WebSocket connection.

Replaces the previous per-message pattern, where every user message opened a
fresh ``ClaudeSDKClient``, threw away its context, and then ran a *second*
inference against the raw Anthropic API purely to stream tokens. Here one client
stays connected for the life of the conversation, so context survives between
turns, and ``include_partial_messages`` gives token-level streaming from the same
pass that runs the tools.

Task layout
-----------
::

    WS receive loop   reads frames; submits Turns; resolves approvals. Never
                      touches the SDK client, and never awaits a turn.
    session runner    connect -> N turns -> disconnect. Owns the client.
    sender task       drains the output queue to the socket.
    SDK internals     tasks spawned by connect(); may call can_use_tool.

The runner exists because ``ClaudeSDKClient.connect()`` enters an anyio task
group, and anyio requires the task that entered a scope to be the one that exits
it. Connecting lazily inside a per-message task and disconnecting from the
WebSocket handler's ``finally`` raises ``RuntimeError`` and leaks the CLI
subprocess. One task owning the whole lifecycle avoids that entirely.

Turns are serialised through a queue rather than cancelled. Cancelling a turn
mid-stream leaves unread messages queued in a *persistent* client, and they then
interleave with the next turn's output.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from errors import sanitize_error_message

from . import events
from .config import SessionConfig, build_options
from .permissions import PermissionBroker
from .translate import TurnState, translate

logger = logging.getLogger(__name__)

# How long to wait for the CLI to wind down after an interrupt before forcing it.
INTERRUPT_GRACE_S = 10.0

# Ceiling on concurrent CLI subprocesses in this process.
_cli_slots: Optional[asyncio.Semaphore] = None
_cli_slots_size = 0


def configure_concurrency(max_concurrent: int) -> None:
    """Set the process-wide cap on live CLI subprocesses. Call once at startup."""
    global _cli_slots, _cli_slots_size
    if _cli_slots is None or _cli_slots_size != max_concurrent:
        _cli_slots = asyncio.Semaphore(max_concurrent)
        _cli_slots_size = max_concurrent
        logger.info("Agent CLI concurrency limit set to %d", max_concurrent)


def _slots() -> asyncio.Semaphore:
    global _cli_slots, _cli_slots_size
    if _cli_slots is None:
        _cli_slots_size = 8
        _cli_slots = asyncio.Semaphore(_cli_slots_size)
    return _cli_slots


def _default_client_factory(
    cfg: SessionConfig,
    can_use_tool: Optional[Any],
    resume: Optional[str],
) -> Any:
    """Build a real SDK client. Imported lazily so tests need no SDK."""
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient(options=build_options(cfg, can_use_tool=can_use_tool, resume=resume))


@dataclass
class Turn:
    """One user message and everything the session learned answering it."""

    prompt: str
    conversation_id: str
    auth_token: Optional[str] = None
    org_id: Optional[str] = None
    project_id: Optional[str] = None

    done: asyncio.Event = field(default_factory=asyncio.Event)

    # Filled in by the runner before done is set.
    text: str = ""
    session_id: Optional[str] = None
    error: Optional[str] = None
    interrupted: bool = False
    cost_usd: Optional[float] = None
    usage: Optional[Dict[str, Any]] = None
    # Tool activity, in order, for the persister to store alongside the reply.
    tool_events: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def auth_key(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        return (self.auth_token, self.org_id, self.project_id)


class ChatSession:
    """A conversation backed by one long-lived Claude Agent SDK client."""

    def __init__(
        self,
        cfg: SessionConfig,
        out_queue: "asyncio.Queue",
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.cfg = cfg
        self._out = out_queue
        # (cfg, can_use_tool, resume) -> an unconnected client. Injectable so the
        # turn loop can be tested without the SDK or a CLI subprocess.
        self._client_factory = client_factory or _default_client_factory

        self.broker: Optional[PermissionBroker] = None
        if cfg.require_tool_approval:
            self.broker = PermissionBroker(
                out_queue,
                timeout_s=cfg.permission_timeout_s,
                allowlist=cfg.allowed_tools,
            )

        self._turns: "asyncio.Queue[Optional[Turn]]" = asyncio.Queue()
        self._client: Any = None
        self._runner: Optional[asyncio.Task] = None
        self._current: Optional[TurnState] = None
        self._carry: Optional[Turn] = None

        self._claude_session_id: Optional[str] = None
        self._closing = False
        self._reset_requested = False
        self._interrupt_requested = False
        self._holds_slot = False
        self._watchdog: Optional[asyncio.Task] = None
        self._pending_model: Optional[str] = None

    # -- state -------------------------------------------------------------

    @property
    def claude_session_id(self) -> Optional[str]:
        """The CLI's session id, for resuming this conversation later."""
        return self._claude_session_id

    @property
    def busy(self) -> bool:
        return self._current is not None

    def seed_session_id(self, claude_session_id: Optional[str]) -> None:
        """Resume a previous conversation on the next message."""
        if claude_session_id:
            self._claude_session_id = claude_session_id
            logger.info("Session will resume Claude session %s", claude_session_id)

    # -- called from the WebSocket receive loop ----------------------------

    def start(self) -> None:
        """Start (or restart) the runner. Connecting waits for real work."""
        if self._runner is None or self._runner.done():
            self._closing = False
            self._runner = asyncio.create_task(
                self._run(), name=f"chat-session-{self.cfg.session_id}"
            )

    async def submit(self, turn: Turn) -> None:
        """Queue a turn. Returns immediately; await ``turn.done`` for the result."""
        self.start()  # self-heals if the runner died
        await self._turns.put(turn)

    async def interrupt(self) -> None:
        """
        Stop the turn in flight.

        Order matters. Pending approvals are released *first*: the SDK's reader
        task may be parked inside ``can_use_tool``, and ``client.interrupt()``
        awaits a control response only that task can deliver. Interrupting first
        would deadlock the socket.
        """
        if self.broker:
            self.broker.deny_all("Interrupted by user")

        if self._current is None:
            # Nothing running - acknowledge so the button feels responsive, and
            # don't send a control request the CLI isn't expecting.
            self._out.put_nowait(events.interrupted())
            return

        self._interrupt_requested = True
        client = self._client
        if client is None:
            return

        try:
            await asyncio.wait_for(client.interrupt(), timeout=5.0)
        except Exception as exc:
            logger.warning("interrupt() failed (%s); falling back to cancel", exc)
            self._force_restart()
            return

        self._arm_watchdog()

    async def set_model(self, model: str) -> bool:
        """
        Switch the model for subsequent turns, keeping the conversation.

        Applied immediately when idle, otherwise deferred until the turn in
        flight finishes. Deliberately never awaited while a turn is running:
        ``client.set_model()`` is a control request whose response only the
        SDK's reader task can deliver, and that task may be parked inside a
        pending tool approval - the same trap as ``interrupt()``.

        Returns True if it took effect now, False if it was queued.
        """
        if model == self.cfg.model and self._pending_model is None:
            return True

        self.cfg.model = model
        self._pending_model = model

        if self._current is not None:
            logger.info("Model switch to %s queued until the current turn ends", model)
            return False

        if self._client is None:
            # Nothing connected yet, so there is nothing to defer: the next
            # connect builds its options from cfg.model, which is already set.
            self._pending_model = None
            logger.info("Model set to %s; applies when the session connects", model)
            return True

        return await self._apply_pending_model()

    async def _apply_pending_model(self) -> bool:
        """Push a queued model change to the live client. Safe only when idle."""
        model, self._pending_model = self._pending_model, None
        if not model or self._client is None:
            return False

        try:
            await asyncio.wait_for(self._client.set_model(model), timeout=10.0)
            logger.info("Model switched to %s for session %s", model, self.cfg.session_id)
            return True
        except Exception as exc:
            # The next reconnect picks it up from cfg.model regardless.
            logger.warning("Live model switch to %s failed (%s); applies on reconnect", model, exc)
            return False

    async def reset(self) -> None:
        """Drop conversation context; the next message starts a new session."""
        self._reset_requested = True
        await self._turns.put(None)

    async def aclose(self) -> None:
        """Shut down the session and its subprocess."""
        self._closing = True
        if self.broker:
            self.broker.close()

        self._cancel_watchdog()
        await self._turns.put(None)

        runner = self._runner
        if runner is None or runner.done():
            return

        try:
            await asyncio.wait_for(asyncio.shield(runner), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("Session runner did not stop in time; cancelling")
            runner.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await runner
        except Exception:
            logger.exception("Session runner failed during shutdown")

    # -- the runner: connect, serve turns, disconnect - all in one task ----

    async def _run(self) -> None:
        try:
            while not self._closing:
                turn = self._carry or await self._turns.get()
                self._carry = None

                if turn is None:
                    if self._closing:
                        break
                    if self._reset_requested:
                        self._reset_requested = False
                        self._claude_session_id = None
                        logger.info("Conversation context cleared")
                    continue

                if not await self._open_for(turn):
                    continue

                try:
                    await self._serve(turn)
                finally:
                    await self._disconnect()
        except asyncio.CancelledError:
            logger.info("Session runner cancelled")
            raise
        except Exception:
            logger.exception("Session runner crashed")
            self._out.put_nowait(
                events.error("The agent session ended unexpectedly. Please try again.")
            )
        finally:
            await self._disconnect()

    async def _serve(self, first: Turn) -> None:
        """Run turns against an open client until idle, reset, or a context change."""
        connected_auth = first.auth_key
        await self._run_turn(first)

        while not self._closing:
            try:
                nxt = await asyncio.wait_for(
                    self._turns.get(), timeout=self.cfg.idle_timeout_s
                )
            except asyncio.TimeoutError:
                logger.info(
                    "Session %s idle for %ss; parking the CLI (resume=%s)",
                    self.cfg.session_id,
                    self.cfg.idle_timeout_s,
                    self._claude_session_id,
                )
                return

            if nxt is None:
                if self._reset_requested:
                    self._reset_requested = False
                    self._claude_session_id = None
                    logger.info("Conversation context cleared")
                return

            if nxt.auth_key != connected_auth:
                # Tool auth is captured in context variables when the client
                # connects, so a tenant switch needs a new client.
                logger.info("Tenant context changed; reconnecting")
                self._carry = nxt
                return

            # Safe point for a model switch requested mid-turn: the previous
            # turn has ended, so no approval can be parked in the SDK reader.
            if self._pending_model:
                await self._apply_pending_model()

            await self._run_turn(nxt)

    async def _open_for(self, turn: Turn) -> bool:
        """Apply the turn's tenant context and connect. False if it failed."""
        self.cfg.auth_token = turn.auth_token or self.cfg.auth_token
        self.cfg.org_id = turn.org_id
        self.cfg.project_id = turn.project_id
        self._apply_tool_context()

        self._out.put_nowait(events.processing())

        try:
            await self._connect()
            return True
        except Exception as exc:
            message = sanitize_error_message(exc, "starting the agent session")
            self._out.put_nowait(events.error(message))
            turn.error = message
            turn.done.set()
            await self._disconnect()
            return False

    def _apply_tool_context(self) -> None:
        """
        Bind tenant context for the incident tools.

        Must happen in this task, before ``connect()``. The tools read context
        variables, which are copied into each task when it is created - so the
        SDK's internal tasks inherit whatever is set here at connect time, and
        nothing set afterwards can reach them.
        """
        try:
            from tools.incidents import set_auth_token, set_org_id, set_project_id
        except ImportError:  # SDK absent - nothing can run anyway, don't mask it
            logger.error("Incident tools unavailable; tenant context not applied")
            return

        set_auth_token(self.cfg.auth_token or "")
        set_org_id(self.cfg.org_id or "")
        set_project_id(self.cfg.project_id or "")

    async def _connect(self) -> None:
        await _slots().acquire()
        self._holds_slot = True

        can_use_tool = self.broker.can_use_tool if self.broker else None
        resume = self._claude_session_id

        if resume:
            try:
                await self._connect_with(can_use_tool, resume)
                return
            except Exception as exc:
                # Session transcripts are stored on the pod that created them,
                # so a restart or a different replica invalidates them. Losing
                # history beats refusing the message.
                logger.warning("Could not resume session %s (%s); starting fresh", resume, exc)
                self._claude_session_id = None

        await self._connect_with(can_use_tool, None)

    async def _connect_with(self, can_use_tool: Any, resume: Optional[str]) -> None:
        client = self._client_factory(self.cfg, can_use_tool, resume)
        await client.connect()
        self._client = client
        logger.info(
            "Agent client connected for session %s (resume=%s, model=%s)",
            self.cfg.session_id,
            resume,
            self.cfg.model,
        )

    async def _disconnect(self) -> None:
        client, self._client = self._client, None

        if client is not None:
            try:
                # Shielded so a cancelled runner still tears down the subprocess
                # instead of orphaning it.
                await asyncio.wait_for(asyncio.shield(client.disconnect()), timeout=10.0)
            except asyncio.CancelledError:
                logger.debug("Disconnect continuing after cancellation")
            except Exception as exc:
                logger.warning("Error disconnecting agent client: %s", exc)

        if self._holds_slot:
            self._holds_slot = False
            _slots().release()

    # -- one turn ----------------------------------------------------------

    async def _run_turn(self, turn: Turn) -> None:
        st = TurnState()
        self._current = st
        started = time.monotonic()

        try:
            await self._client.query(prompt=turn.prompt)

            async for message in self._client.receive_response():
                for event in translate(message, st):
                    self._out.put_nowait(event)

                if st.session_id and st.session_id != self._claude_session_id:
                    self._claude_session_id = st.session_id

        except asyncio.CancelledError:
            st.interrupted = True
            self._finish(turn, st)
            raise
        except Exception as exc:
            st.error = sanitize_error_message(exc, "during agent turn")
        finally:
            self._cancel_watchdog()
            self._current = None
            if not turn.done.is_set():
                self._finish(turn, st)
            logger.info(
                "Turn finished in %.1fs (tools=%d, cost=%s, interrupted=%s)",
                time.monotonic() - started,
                len(st.tool_calls),
                st.cost_usd,
                turn.interrupted,
            )

    def _finish(self, turn: Turn, st: TurnState) -> None:
        """
        Close out a turn: record the result and emit exactly one terminal event.

        Every path through ``_run_turn`` lands here, which is what guarantees the
        client's spinner always stops.
        """
        turn.text = st.text_for_persistence
        turn.session_id = st.session_id or self._claude_session_id
        turn.error = st.error
        turn.interrupted = st.interrupted or self._interrupt_requested
        turn.cost_usd = st.cost_usd
        turn.usage = st.usage
        turn.tool_events = st.tool_events

        if turn.interrupted:
            self._out.put_nowait(events.interrupted())
        elif st.error:
            self._out.put_nowait(events.error(st.error))
        else:
            self._out.put_nowait(
                events.complete(
                    session_id=turn.session_id,
                    cost_usd=st.cost_usd,
                    usage=st.usage,
                )
            )

        self._interrupt_requested = False
        turn.done.set()

    # -- interrupt backstop ------------------------------------------------

    def _arm_watchdog(self) -> None:
        self._cancel_watchdog()
        self._watchdog = asyncio.create_task(self._watch_interrupt())

    def _cancel_watchdog(self) -> None:
        watchdog, self._watchdog = self._watchdog, None
        if watchdog and not watchdog.done():
            watchdog.cancel()

    async def _watch_interrupt(self) -> None:
        """Force a restart if the CLI ignores an interrupt."""
        try:
            await asyncio.sleep(INTERRUPT_GRACE_S)
        except asyncio.CancelledError:
            return

        if self._current is not None:
            logger.warning(
                "Turn still running %ss after interrupt; forcing a restart",
                INTERRUPT_GRACE_S,
            )
            self._force_restart()

    def _force_restart(self) -> None:
        """
        Last resort: drop the runner and its subprocess.

        The session id is kept, so the next message reconnects and resumes.
        ``submit()`` restarts the runner on demand.
        """
        runner = self._runner
        if runner and not runner.done():
            runner.cancel()
