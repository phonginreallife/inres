"""
Interactive tool approval: which tools may run without a prompt, pattern matching, and
WebSocket wait/resume for user allow/deny.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Built-in tools considered read-only / low blast-radius — no approval prompt by default.
DEFAULT_PRE_APPROVED_TOOLS: Set[str] = frozenset(
    {
        "get_current_time",
        "get_incident_stats",
        "get_incidents_by_time",
        "get_incident_by_id",
        "search_incidents",
        "release_integration_guide",
        "release_get_status",
        "release_list_yaml_files",
    }
)

def pattern_allows_tool(pattern: str, tool_name: str) -> bool:
    """
    Match a stored user pattern to an Anthropic tool name.

    Supports:
    - Exact tool name
    - ``SomeTool(*)`` meaning exactly that tool (from frontend "always allow")
    - Glob patterns with ``*`` / ``?`` (fnmatch)
    - Legacy ``server:tool(*)`` for MCP — maps to ``mcp__server__tool`` when unambiguous
    """
    p = (pattern or "").strip()
    t = (tool_name or "").strip()
    if not p or not t:
        return False
    if p == t:
        return True
    if p.endswith("(*)"):
        base = p[:-3].strip()
        if t == base:
            return True
        # Legacy MCP display pattern "coralogix:search_logs(*)" → mcp__coralogix__search_logs
        if ":" in base and not base.startswith("mcp__"):
            server, _, mtool = base.partition(":")
            if server and mtool:
                candidate = f"mcp__{server}__{mtool}"
                if t == candidate:
                    return True
                if fnmatch.fnmatch(t, f"mcp__{server}__*{mtool}*"):
                    return True
    if any(ch in p for ch in "*?["):
        return fnmatch.fnmatch(t, p)
    return False


class ToolApprovalPolicy:
    """Decides if a tool may run without showing an approval prompt."""

    def __init__(
        self,
        user_patterns: Optional[List[str]] = None,
        extra_pre_approved: Optional[Set[str]] = None,
    ):
        self._user_patterns: List[str] = list(user_patterns or [])
        self._runtime_patterns: List[str] = []
        self._extra: Set[str] = set(extra_pre_approved or ())

    def add_runtime_pattern(self, pattern: str) -> None:
        p = (pattern or "").strip()
        if not p:
            return
        self._runtime_patterns.append(p)
        logger.info("Runtime tool-allow pattern added: %s", p)

    def is_pre_approved(self, tool_name: str) -> bool:
        if tool_name in self._extra:
            return True
        if tool_name in DEFAULT_PRE_APPROVED_TOOLS:
            return True
        for pat in self._user_patterns + self._runtime_patterns:
            if pattern_allows_tool(pat, tool_name):
                return True
        return False

    def needs_prompt(self, tool_name: str) -> bool:
        """True if the user should be asked before running this tool."""
        return not self.is_pre_approved(tool_name)


class ToolApprovalSession:
    """Wait for WebSocket ``permission_response`` keyed by ``request_id``."""

    def __init__(self, wait_timeout_seconds: float = 900.0):
        self._wait_timeout = wait_timeout_seconds
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    def resolve(self, request_id: str, allowed: bool) -> None:
        rid = str(request_id)
        fut = self._pending.pop(rid, None)
        if fut is not None and not fut.done():
            fut.set_result(bool(allowed))
            logger.debug("Tool approval resolved request_id=%s allowed=%s", rid, allowed)

    def cancel_all(self) -> None:
        for rid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result(False)
        self._pending.clear()
        logger.debug("Tool approval: cancelled all pending")

    async def wait_for_decision(self, request_id: str) -> bool:
        rid = str(request_id)
        async with self._lock:
            if rid in self._pending:
                logger.warning("Duplicate approval request_id=%s", rid)
            loop = asyncio.get_event_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[rid] = fut
        try:
            return await asyncio.wait_for(fut, timeout=self._wait_timeout)
        except asyncio.TimeoutError:
            logger.warning("Tool approval timeout request_id=%s", rid)
            self._pending.pop(rid, None)
            return False
        except asyncio.CancelledError:
            self._pending.pop(rid, None)
            raise
