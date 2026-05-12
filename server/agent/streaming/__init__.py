"""
Streaming / MCP integration (stdio MCP pool and tool routing helpers).

The interactive agent implementation lives in ``inres_agent.InResAgent``.
"""

from .mcp_client import MCPToolManager, MCPServerPool, get_mcp_pool
from .mcp_config import MCPConfigManager

__all__ = [
    "MCPToolManager",
    "MCPServerPool",
    "get_mcp_pool",
    "MCPConfigManager",
]
