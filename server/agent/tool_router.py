"""
Routes Anthropic tool calls to built-in handlers (incidents, release) or stdio MCP tools.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from streaming.mcp_client import MCPToolManager

logger = logging.getLogger(__name__)

ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


def format_tool_result(payload: Dict[str, Any]) -> Tuple[str, bool]:
    """Convert incident/release tool return dict to (string_content, is_error) for Anthropic API."""
    is_err = bool(payload.get("isError"))
    blocks = payload.get("content") or []
    texts: List[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        text = json.dumps(payload, default=str)
    return text, is_err


class ToolRouter:
    """Merges built-in tool handlers with MCP-prefixed tools from MCPToolManager."""

    def __init__(
        self,
        mcp_manager: Optional[MCPToolManager],
        builtin_handlers: Dict[str, ToolHandler],
        builtin_schemas: List[Dict[str, Any]],
    ):
        self.mcp_manager = mcp_manager or MCPToolManager()
        self._handlers = dict(builtin_handlers)
        self._builtin_schemas = list(builtin_schemas)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        schemas.extend(self._builtin_schemas)
        schemas.extend(self.mcp_manager.get_all_tools())
        return schemas

    @property
    def server_count(self) -> int:
        return self.mcp_manager.server_count

    async def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> Tuple[str, bool]:
        if tool_name in self._handlers:
            raw = await self._handlers[tool_name](tool_input)
            return format_tool_result(raw)
        if tool_name.startswith("mcp__"):
            try:
                text = await self.mcp_manager.call_tool(tool_name, tool_input)
                return text, False
            except Exception as e:
                logger.error("MCP tool %s failed: %s", tool_name, e, exc_info=True)
                return json.dumps({"error": str(e)}), True
        return f"Unknown tool: {tool_name}", True
