"""
Session configuration and ``ClaudeAgentOptions`` construction.

Everything the SDK needs to start a session is assembled here, so the options
are defined once instead of being spelled out differently at each WebSocket
endpoint (which is how ``/ws/chat`` and ``/ws/secure/chat`` drifted apart).

The SDK is imported lazily inside :func:`build_options` so this module - and the
tests that import it - work without ``claude-agent-sdk`` installed.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """You are an AI assistant specialized in incident response and DevOps.
You help users manage incidents, analyze alerts, and troubleshoot issues.

**Incident Management Tools:**
- get_incidents_by_time: Fetch incidents within a time range
- get_incident_by_id: Get detailed incident information
- get_incident_stats: Get incident statistics
- get_current_time: Get current time for time-based queries
- search_incidents: Full-text search for incidents

**External Integrations (MCP):** any MCP tools the user has configured, such as
log search or documentation lookup.

Be concise but thorough in your responses."""


@dataclass
class SessionConfig:
    """Per-connection settings for one ``ChatSession``."""

    user_id: str
    session_id: str

    # Tenant context. Changing any of these requires a reconnect - see
    # ChatSession for why.
    auth_token: Optional[str] = None
    org_id: Optional[str] = None
    project_id: Optional[str] = None

    model: str = "claude-opus-5"
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    permission_mode: str = "default"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    # Tool approval. When False the SDK decides on its own and the user is
    # never prompted.
    require_tool_approval: bool = True
    permission_timeout_s: float = 300.0
    allowed_tools: List[str] = field(default_factory=list)

    # Drop the CLI subprocess after this long with no traffic, keeping the
    # session id so the next message resumes.
    idle_timeout_s: float = 900.0

    # External MCP servers, as returned by MCPToolManager.get_server_configs().
    external_mcp: Dict[str, Any] = field(default_factory=dict)

    # Audit hooks, as returned by audit.build_hooks_config().
    hooks: Optional[Dict[str, Any]] = None

    @property
    def auth_key(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Identity of the current tenant context.

        The tools read auth from context variables, which are copied into the
        SDK's tasks when the client connects. A later change cannot reach those
        tasks, so ChatSession compares this key and reconnects when it moves.
        """
        return (self.auth_token, self.org_id, self.project_id)


def normalize_mcp_servers(configs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Put external MCP server configs into the shape the CLI expects.

    ``MCPToolManager.get_server_configs()`` adds a ``tools`` key holding the
    tools it discovered. That is not part of the MCP config schema and the CLI
    rejects or ignores it, so it is stripped here, and stdio servers are tagged
    explicitly.
    """
    normalized: Dict[str, Any] = {}

    for name, conf in (configs or {}).items():
        if not isinstance(conf, dict):
            # Already an SDK server object (in-process MCP server).
            normalized[name] = conf
            continue

        if "command" in conf:
            normalized[name] = {
                "type": "stdio",
                "command": conf["command"],
                "args": conf.get("args", []),
                "env": conf.get("env", {}) or {},
            }
        elif "url" in conf:
            normalized[name] = {
                "type": conf.get("type", "http"),
                "url": conf["url"],
                "headers": conf.get("headers", {}) or {},
            }
        else:
            logger.warning("Skipping MCP server %r: no command or url", name)

    return normalized


def build_options(
    cfg: SessionConfig,
    can_use_tool: Optional[Callable[..., Any]] = None,
    resume: Optional[str] = None,
) -> Any:
    """
    Build ``ClaudeAgentOptions`` for one connection.

    Several settings here are load-bearing and easy to get wrong:

    ``system_prompt``
        Passed as a preset with an ``append``. A bare string *replaces* the
        Claude Code preset, which silently removes the built-in tools - Read,
        Write, Bash, Grep and the rest - and leaves only our MCP tools.

    ``setting_sources``
        ``["project"]`` only. Including ``"user"`` reads ``$HOME/.claude``,
        which in the container is ``/root`` and shared by every tenant.

    ``cwd``
        The user's workspace, so the memory/skills/plugins the sync endpoints
        write are actually visible to the agent.

    ``include_partial_messages``
        The reason token streaming works at all.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    from tools.incidents import create_incident_tools_server

    mcp_servers: Dict[str, Any] = {"incident_tools": create_incident_tools_server()}
    mcp_servers.update(normalize_mcp_servers(cfg.external_mcp))

    permission_mode = cfg.permission_mode
    if can_use_tool is None and permission_mode == "default":
        # "default" means the CLI asks before running a protected tool. With no
        # callback there is nobody to ask, so the tool would simply never run.
        # Disabling approval is an explicit operator choice, so honour it.
        permission_mode = "bypassPermissions"
        logger.warning(
            "Tool approval is disabled; running with permission_mode=bypassPermissions"
        )

    options_kwargs: Dict[str, Any] = {
        "model": cfg.model,
        "system_prompt": {
            "type": "preset",
            "preset": "claude_code",
            "append": cfg.system_prompt,
        },
        "mcp_servers": mcp_servers,
        "permission_mode": permission_mode,
        "setting_sources": ["project"],
        "include_partial_messages": True,
        "env": _sdk_env(),
    }

    workspace = _workspace_for(cfg.user_id)
    if workspace:
        options_kwargs["cwd"] = workspace

    if can_use_tool is not None:
        options_kwargs["can_use_tool"] = can_use_tool
    if cfg.allowed_tools:
        options_kwargs["allowed_tools"] = list(cfg.allowed_tools)
    if cfg.hooks:
        options_kwargs["hooks"] = cfg.hooks
    if cfg.max_turns:
        options_kwargs["max_turns"] = cfg.max_turns
    if cfg.max_budget_usd:
        options_kwargs["max_budget_usd"] = cfg.max_budget_usd
    if resume:
        options_kwargs["resume"] = resume

    return ClaudeAgentOptions(**options_kwargs)


def _sdk_env() -> Dict[str, str]:
    """
    Extra environment for the CLI subprocess.

    Additive, not exhaustive: the SDK spawns the CLI with ``{**os.environ,
    **options.env}``, so everything this process already has is inherited. This
    only pins the values the agent genuinely depends on.

    That inheritance is also how authentication works. The CLI accepts either
    ``ANTHROPIC_API_KEY`` or ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude
    setup-token``), or a login stored in ``$HOME/.claude``. An API key takes
    precedence, so it must be genuinely absent - note that config/loader.py
    copies a non-empty ``anthropic_api_key`` from the YAML into the
    environment - for the OAuth token to be used.
    """
    env: Dict[str, str] = {}
    for key in ("ANTHROPIC_API_KEY", "inres_API_URL", "inres_API_KEY"):
        value = os.getenv(key)
        if value:
            env[key] = value
    return env


def _workspace_for(user_id: str) -> Optional[str]:
    """
    The agent's working directory, or None if it can't be prepared.

    A missing workspace is not fatal - the agent still runs, it just won't see
    the user's synced skills and memory - so failures are logged, not raised.
    """
    if not user_id:
        return None
    try:
        from services.storage import ensure_user_workspace

        return str(ensure_user_workspace(user_id))
    except Exception as exc:
        logger.warning("Could not prepare workspace for %s: %s", user_id, exc)
        return None
