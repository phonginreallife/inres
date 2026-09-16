"""
Claude Agent SDK message types, with a structural fallback.

The translator dispatches on ``isinstance`` against the SDK's message and
content-block dataclasses. Importing them directly would make
``session/translate.py`` - and therefore its unit tests - require the
``claude-agent-sdk`` package plus the Claude Code CLI it drives. The tests are
meant to run anywhere, so this module resolves the types once:

* If ``claude_agent_sdk`` imports cleanly, the real classes are re-exported and
  everything downstream operates on genuine SDK objects.
* Otherwise a set of structurally identical dataclasses stands in, so the
  translator and its tests still work off the same names.

The fallback is all-or-nothing. A partial import would mix real and shim types
and make ``isinstance`` silently wrong, which is far worse than not importing at
all.

Two details worth remembering:

* ``StreamEvent`` is not in ``claude_agent_sdk.__all__``; it has to come from
  ``claude_agent_sdk.types``.
* The SDK's content blocks carry no ``type`` field. Testing ``hasattr(block,
  "type")`` is how the previous orchestrator silently dropped every tool call.

Shim shapes mirror claude-agent-sdk 0.2.x. If that pin moves, re-check them.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

SDK_AVAILABLE = False

try:  # pragma: no cover - depends on the deployment environment
    from claude_agent_sdk import (  # type: ignore
        AssistantMessage,
        PermissionResultAllow,
        PermissionResultDeny,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    from claude_agent_sdk.types import StreamEvent  # type: ignore

    SDK_AVAILABLE = True
except Exception:  # ImportError, or a version whose surface has moved
    @dataclass
    class TextBlock:  # type: ignore[no-redef]
        text: str

    @dataclass
    class ThinkingBlock:  # type: ignore[no-redef]
        thinking: str
        signature: str = ""

    @dataclass
    class ToolUseBlock:  # type: ignore[no-redef]
        id: str
        name: str
        input: Dict[str, Any]

    @dataclass
    class ToolResultBlock:  # type: ignore[no-redef]
        tool_use_id: str
        content: Union[str, List[Dict[str, Any]], None] = None
        is_error: Optional[bool] = None

    @dataclass
    class UserMessage:  # type: ignore[no-redef]
        content: Union[str, List[Any]]
        parent_tool_use_id: Optional[str] = None

    @dataclass
    class AssistantMessage:  # type: ignore[no-redef]
        content: List[Any]
        model: str = ""
        parent_tool_use_id: Optional[str] = None

    @dataclass
    class SystemMessage:  # type: ignore[no-redef]
        subtype: str
        data: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ResultMessage:  # type: ignore[no-redef]
        # These six are required in the real SDK. Giving them defaults here
        # would let tests construct a message the real class rejects, which is
        # exactly the drift this module is supposed to make impossible.
        subtype: str
        duration_ms: int
        duration_api_ms: int
        is_error: bool
        num_turns: int
        session_id: str
        total_cost_usd: Optional[float] = None
        usage: Optional[Dict[str, Any]] = None
        result: Optional[str] = None
        structured_output: Any = None

    @dataclass
    class StreamEvent:  # type: ignore[no-redef]
        uuid: str
        session_id: str
        event: Dict[str, Any]
        parent_tool_use_id: Optional[str] = None

    @dataclass
    class PermissionResultAllow:  # type: ignore[no-redef]
        updated_input: Optional[Dict[str, Any]] = None
        updated_permissions: Optional[List[Any]] = None

    @dataclass
    class PermissionResultDeny:  # type: ignore[no-redef]
        message: str = ""
        interrupt: bool = False


__all__ = [
    "SDK_AVAILABLE",
    "AssistantMessage",
    "PermissionResultAllow",
    "PermissionResultDeny",
    "ResultMessage",
    "StreamEvent",
    "SystemMessage",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "UserMessage",
]
