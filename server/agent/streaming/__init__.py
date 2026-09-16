"""
MCP client plumbing.

Named for the token-streaming agent that used to live here; streaming is now
handled by the Claude Agent SDK inside the ``session`` package, and what remains
is the pool that manages the user's external MCP servers.

Components:
- mcp_client.py: MCP server pool for external tool integrations
- mcp_config.py: MCP server configuration

Usage:
    from streaming import MCPToolManager, get_mcp_pool
"""

from .mcp_client import MCPToolManager, MCPServerPool, get_mcp_pool
from .mcp_config import MCPConfigManager

__all__ = [
    "MCPToolManager",
    "MCPServerPool",
    "get_mcp_pool",
    "MCPConfigManager",
]
