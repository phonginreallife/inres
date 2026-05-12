"""
Hybrid Agent Package (Anthropic streaming + built-in tools + MCP).

The production agent is ``InResAgent`` (also exported as ``SDKHybridAgent`` for compatibility).
"""

from inres_agent import InResAgent, InResAgentConfig
from tool_router import ToolRouter

from .sdk_agent import SDKHybridAgent, SDKHybridAgentConfig

__all__ = [
    "InResAgent",
    "InResAgentConfig",
    "ToolRouter",
    "SDKHybridAgent",
    "SDKHybridAgentConfig",
]
