"""
WebSocket event payloads.

Every frame the agent pushes to a browser is built here, so the wire contract
lives in one place instead of being spelled out at each emit site. Two rules
hold for everything in this module:

1. Payloads must be JSON-serialisable. ``websocket.send_json`` raises on
   anything else, and the sender task treats a raise as fatal - a single bad
   frame silences the socket for the rest of the session. ``jsonable()`` is the
   guard, and it is applied to every value that originates outside our code
   (tool inputs, tool results, permission suggestions).
2. Terminal events (``complete`` / ``error`` / ``interrupted``) are emitted by
   ChatSession's turn ``finally`` block, never by the translator, so exactly one
   of them is sent per turn.

The shapes here are dictated by the live consumer,
``frontend/inres/src/hooks/useClaudeWebSocket.js``. Where it and the SDK
disagree, the frontend wins - see ``tool_use()``.
"""

import dataclasses
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tool output can run to megabytes (Bash, Grep, log queries). Past this we send
# a truncated copy and keep the full text for persistence only.
MAX_TOOL_RESULT_CHARS = 8000


def jsonable(value: Any, _depth: int = 0) -> Any:
    """
    Coerce a value into something ``json.dumps`` accepts.

    Anything reaching the socket may have come from the SDK (dataclasses such as
    ``PermissionUpdate``), from an MCP server, or from a tool's return value, so
    nothing can be assumed serialisable. Unknown objects degrade to ``repr`` -
    a slightly ugly frame beats a dead socket.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if _depth > 8:  # cycle guard; nothing legitimate nests this deep
        return repr(value)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return jsonable(dataclasses.asdict(value), _depth + 1)
        except Exception:
            return repr(value)

    if isinstance(value, dict):
        return {str(k): jsonable(v, _depth + 1) for k, v in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v, _depth + 1) for v in value]

    if hasattr(value, "model_dump"):  # pydantic, as used by mcp
        try:
            return jsonable(value.model_dump(), _depth + 1)
        except Exception:
            return repr(value)

    return repr(value)


def _as_text(content: Any) -> str:
    """Flatten a tool result's content into a string for display."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(str(jsonable(item)))
        return "\n".join(parts)
    return str(jsonable(content))


# --------------------------------------------------------------------------
# Streaming events
# --------------------------------------------------------------------------


def delta(text: str) -> Dict[str, Any]:
    """One increment of assistant text."""
    return {"type": "delta", "content": text}


def thinking(accumulated: str) -> Dict[str, Any]:
    """
    Reasoning text.

    The frontend *replaces* its thinking buffer on each of these rather than
    appending, so callers must pass the running total, not the increment.
    """
    return {"type": "thinking", "content": accumulated}


def tool_use(tool_id: str, name: str, tool_input: Any) -> Dict[str, Any]:
    """
    A tool call.

    Carries the payload twice on purpose. ``useClaudeWebSocket.js`` reads
    ``data.content`` and JSON-parses it, while the flat ``id``/``name``/``input``
    keys are the shape the SDK uses and what any new consumer would reach for.
    Sending both costs a few bytes and keeps either style working.
    """
    safe_input = jsonable(tool_input)
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": safe_input,
        "content": {"id": tool_id, "name": name, "input": safe_input},
    }


def tool_result(
    tool_use_id: str,
    content: Any,
    is_error: bool = False,
) -> Dict[str, Any]:
    """A tool's output, truncated for transport."""
    text = _as_text(content)
    truncated = len(text) > MAX_TOOL_RESULT_CHARS
    if truncated:
        omitted = len(text) - MAX_TOOL_RESULT_CHARS
        text = text[:MAX_TOOL_RESULT_CHARS] + f"\n... [{omitted} characters truncated]"

    event: Dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": text,
        "is_error": bool(is_error),
    }
    if truncated:
        event["truncated"] = True
    return event


def todo_update(todos: Any) -> Dict[str, Any]:
    """Task-list state, rendered as a checklist by the UI."""
    return {"type": "todo_update", "todos": jsonable(todos)}


# --------------------------------------------------------------------------
# Terminal events - exactly one per turn, emitted by ChatSession
# --------------------------------------------------------------------------


def complete(
    session_id: Optional[str] = None,
    cost_usd: Optional[float] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The turn finished normally."""
    event: Dict[str, Any] = {"type": "complete"}
    if session_id:
        event["session_id"] = session_id
    if cost_usd is not None:
        event["cost_usd"] = cost_usd
    if usage:
        event["usage"] = jsonable(usage)
    return event


def error(message: str) -> Dict[str, Any]:
    """The turn failed. Message should already be sanitised for the client."""
    return {"type": "error", "error": message}


def interrupted() -> Dict[str, Any]:
    """The turn was stopped by the user."""
    return {"type": "interrupted"}


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------


def session_init(session_id: str) -> Dict[str, Any]:
    """The underlying Claude session id, once the CLI reports it."""
    return {"type": "session_init", "session_id": session_id}


def processing() -> Dict[str, Any]:
    """Work has started - covers the reconnect latency after an idle park."""
    return {"type": "processing"}


def model_changed(
    model: str,
    available: Optional[List[Dict[str, str]]] = None,
    pending: bool = False,
) -> Dict[str, Any]:
    """
    Confirm the active model.

    ``pending`` means the switch is queued behind the turn currently running
    and takes effect on the next one, so the UI can say so rather than
    implying the in-flight answer came from the new model.
    """
    return {
        "type": "model_changed",
        "model": model,
        "available_models": available or [],
        "pending": pending,
    }


def history_cleared(conversation_id: str) -> Dict[str, Any]:
    """Context dropped; the client should start a new conversation thread."""
    return {
        "type": "history_cleared",
        "message": "Conversation history cleared",
        "conversation_id": conversation_id,
    }


def ping(timestamp: float) -> Dict[str, Any]:
    """Keepalive."""
    return {"type": "ping", "timestamp": timestamp}


# --------------------------------------------------------------------------
# Tool approval
# --------------------------------------------------------------------------


def permission_request(
    request_id: str,
    tool_name: str,
    input_data: Any,
    suggestions: Any = None,
) -> Dict[str, Any]:
    """
    Ask the user to approve a tool call.

    ``input_data`` and ``tool_input`` are the same value under both names -
    the frontend reads ``data.input_data || data.tool_input``.
    """
    safe_input = jsonable(input_data)
    return {
        "type": "permission_request",
        "request_id": request_id,
        "tool_name": tool_name,
        "input_data": safe_input,
        "tool_input": safe_input,
        "suggestions": jsonable(suggestions or []),
    }


def permission_timeout(request_id: str, tool_name: str = "") -> Dict[str, Any]:
    """Nobody answered in time; the call was denied and the chip can clear."""
    return {
        "type": "permission_timeout",
        "request_id": request_id,
        "tool_name": tool_name,
    }
