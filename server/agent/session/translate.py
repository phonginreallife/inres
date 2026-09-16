"""
SDK message stream -> WebSocket events.

This is the heart of the streaming path and deliberately the dullest part of it:
a pure function plus a mutable ``TurnState``, with no I/O, no SDK client and no
asyncio. Everything that can be got wrong about the stream is decided here, so
it can be tested exhaustively against synthetic message sequences.

What the SDK gives us, per turn:

    SystemMessage(subtype="init")   session id
    StreamEvent * N                 raw Anthropic events - token deltas
    AssistantMessage * M            complete messages; M > 1 when tools are used
    UserMessage * M-1               tool results, despite the "user" role
    ResultMessage                   session id, cost, usage; ends the turn

Two things are easy to get wrong:

**Double-emitted text.** ``StreamEvent`` deltas and the ``AssistantMessage`` that
follows carry the same text. Emitting both duplicates the whole reply; emitting
only the deltas loses text whenever the CLI skips partials. So: stream the
deltas, treat the ``AssistantMessage`` as authoritative, and emit only the
difference between them (normally nothing).

**Subagent output.** When Claude delegates to a subagent, that subagent's text
arrives with ``parent_tool_use_id`` set. It is inner monologue, not the answer,
and merging it produces a reply interleaved with work-in-progress.

Terminal events are not produced here. ChatSession emits exactly one of
``complete`` / ``error`` / ``interrupted`` from its turn ``finally`` block, so
every path through a turn ends the UI's spinner exactly once.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import events
from ._sdk_types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

logger = logging.getLogger(__name__)


@dataclass
class TurnState:
    """
    Everything accumulated while a single turn streams.

    ``assistant_text`` and ``stream_buf`` are separate on purpose:
    ``assistant_text`` holds messages the SDK has completed, ``stream_buf`` holds
    the one still arriving. An interrupt lands between the two, and
    ``text_for_persistence`` is what survives it.
    """

    # Text of every AssistantMessage completed so far this turn. A tool-using
    # turn produces several; the reply the user saw is their concatenation.
    assistant_text: str = ""

    # Text streamed for the assistant message currently in flight. Cleared when
    # that message completes.
    stream_buf: str = ""

    # Reasoning, accumulated because the client replaces rather than appends.
    thinking_buf: str = ""

    session_id: Optional[str] = None
    error: Optional[str] = None
    interrupted: bool = False

    cost_usd: Optional[float] = None
    usage: Optional[Dict[str, Any]] = None
    num_turns: int = 0

    # Tool calls seen this turn, for logging and audit correlation.
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def text_for_persistence(self) -> str:
        """
        The assistant text to store.

        Includes the in-flight buffer so an interrupted turn keeps the partial
        answer the user actually read.
        """
        return self.assistant_text + self.stream_buf


def translate(message: Any, st: TurnState) -> List[Dict[str, Any]]:
    """
    Convert one SDK message into zero or more WebSocket events.

    Mutates ``st``. Never raises on unexpected shapes - an unknown message is
    ignored rather than killing the turn, because the CLI may add message types
    in a version bump.
    """
    try:
        if isinstance(message, StreamEvent):
            return _stream_event(message, st)
        if isinstance(message, AssistantMessage):
            return _assistant(message, st)
        if isinstance(message, UserMessage):
            return _user(message, st)
        if isinstance(message, SystemMessage):
            return _system(message, st)
        if isinstance(message, ResultMessage):
            return _result(message, st)
    except Exception:
        logger.exception("Failed to translate SDK message %s", type(message).__name__)
        return []

    logger.debug("Ignoring unhandled SDK message type %s", type(message).__name__)
    return []


def _stream_event(msg: StreamEvent, st: TurnState) -> List[Dict[str, Any]]:
    if getattr(msg, "parent_tool_use_id", None) is not None:
        return []  # subagent output - not part of the answer

    if not st.session_id and getattr(msg, "session_id", None):
        st.session_id = msg.session_id

    event = msg.event or {}
    kind = event.get("type")

    if kind == "message_start":
        # A new assistant message begins; anything buffered belonged to the
        # previous one and has already been folded into assistant_text.
        st.stream_buf = ""
        return []

    if kind != "content_block_delta":
        # content_block_start/stop, message_delta, message_stop: nothing to show.
        return []

    delta = event.get("delta") or {}
    delta_kind = delta.get("type")

    if delta_kind == "text_delta":
        text = delta.get("text") or ""
        if not text:
            return []
        st.stream_buf += text
        return [events.delta(text)]

    if delta_kind == "thinking_delta":
        st.thinking_buf += delta.get("thinking") or ""
        return [events.thinking(st.thinking_buf)]

    # signature_delta carries a thinking signature; input_json_delta carries
    # partial, unparseable tool arguments. Neither is displayable.
    return []


def _assistant(msg: AssistantMessage, st: TurnState) -> List[Dict[str, Any]]:
    if getattr(msg, "parent_tool_use_id", None) is not None:
        return []

    out: List[Dict[str, Any]] = []
    blocks = msg.content or []

    final = "".join(b.text for b in blocks if isinstance(b, TextBlock))
    if final:
        out.extend(_reconcile_text(final, st))

    # The in-flight message is done either way; the next message_start will
    # begin a fresh buffer.
    st.stream_buf = ""

    for block in blocks:
        if isinstance(block, ToolUseBlock):
            st.tool_calls.append({"id": block.id, "name": block.name})
            out.append(events.tool_use(block.id, block.name, block.input))
            if block.name == "TodoWrite":
                todos = (block.input or {}).get("todos", [])
                out.append(events.todo_update(todos))
        elif isinstance(block, ThinkingBlock):
            # Only when partials were off - otherwise thinking_deltas covered it
            # and re-emitting would rewind the UI to a stale snapshot.
            if not st.thinking_buf:
                st.thinking_buf = block.thinking
                out.append(events.thinking(block.thinking))

    return out


def _reconcile_text(final: str, st: TurnState) -> List[Dict[str, Any]]:
    """
    Emit whatever of ``final`` was not already streamed.

    Normal case: the deltas add up to exactly ``final`` and nothing is emitted.
    Partials disabled: nothing was streamed, so the whole message goes out in one
    frame - degraded, but not broken. Genuine divergence: emit nothing, because
    duplicating text is worse than dropping a correction, and log it loudly since
    it means an assumption about the CLI no longer holds.
    """
    out: List[Dict[str, Any]] = []

    if final.startswith(st.stream_buf):
        missing = final[len(st.stream_buf):]
    elif not st.stream_buf:
        missing = final
    else:
        logger.warning(
            "Streamed text diverged from final message "
            "(streamed %d chars, final %d chars); suppressing re-emit",
            len(st.stream_buf),
            len(final),
        )
        missing = ""

    if missing:
        out.append(events.delta(missing))

    st.assistant_text += final
    return out


def _user(msg: UserMessage, st: TurnState) -> List[Dict[str, Any]]:
    if getattr(msg, "parent_tool_use_id", None) is not None:
        return []  # a subagent's tool results

    content = msg.content
    if not isinstance(content, list):
        return []  # the CLI echoing our own prompt back

    out: List[Dict[str, Any]] = []
    for block in content:
        if isinstance(block, ToolResultBlock):
            out.append(
                events.tool_result(
                    block.tool_use_id,
                    block.content,
                    bool(block.is_error),
                )
            )
    return out


def _system(msg: SystemMessage, st: TurnState) -> List[Dict[str, Any]]:
    if msg.subtype != "init":
        return []

    session_id = (msg.data or {}).get("session_id")
    if not session_id:
        return []

    st.session_id = session_id
    return [events.session_init(session_id)]


def _result(msg: ResultMessage, st: TurnState) -> List[Dict[str, Any]]:
    if getattr(msg, "session_id", None):
        st.session_id = msg.session_id

    st.cost_usd = getattr(msg, "total_cost_usd", None)
    st.usage = getattr(msg, "usage", None)
    st.num_turns = getattr(msg, "num_turns", 0) or 0

    if getattr(msg, "is_error", False):
        st.error = msg.result or f"Agent run failed ({msg.subtype})"
    elif not st.text_for_persistence and msg.result:
        # No assistant text streamed but the run produced a result string -
        # take it so the turn isn't recorded as empty.
        st.assistant_text = msg.result

    # The terminal event belongs to ChatSession.
    return []
