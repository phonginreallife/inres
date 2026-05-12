"""
Backward-compatible aliases for the Anthropic-only InRes agent.

Prefer importing ``InResAgent`` / ``InResAgentConfig`` from ``inres_agent``.
"""

from inres_agent import InResAgent, InResAgentConfig

SDKHybridAgent = InResAgent
SDKHybridAgentConfig = InResAgentConfig

__all__ = [
    "InResAgent",
    "InResAgentConfig",
    "SDKHybridAgent",
    "SDKHybridAgentConfig",
]
