"""
Connection-scoped agent sessions.

One ``ChatSession`` per WebSocket, owning one long-lived ``ClaudeSDKClient`` and
translating its message stream into the events the browser consumes.

Module map:

    _sdk_types.py   SDK message/block types, with a shim when the SDK is absent
    events.py       every WebSocket payload the agent can emit
    translate.py    pure SDK-message -> event translation (the tested core)
    permissions.py  tool-approval broker
    config.py       SessionConfig and ClaudeAgentOptions construction
    session.py      ChatSession: client lifecycle and the turn loop
"""

from .config import DEFAULT_SYSTEM_PROMPT, SessionConfig, build_options, normalize_mcp_servers
from .events import MAX_TOOL_RESULT_CHARS
from .permissions import PermissionBroker
from .session import ChatSession, Turn, configure_concurrency
from .translate import TurnState, translate

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "MAX_TOOL_RESULT_CHARS",
    "ChatSession",
    "PermissionBroker",
    "SessionConfig",
    "Turn",
    "TurnState",
    "build_options",
    "configure_concurrency",
    "normalize_mcp_servers",
    "translate",
]
