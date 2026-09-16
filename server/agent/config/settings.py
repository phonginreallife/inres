"""
Centralized Configuration Module

Loads configuration from unified dev.config.yaml and provides
a singleton config object for use across all modules.

Usage:
    from config import config

    db = psycopg2.connect(config.database_url)
    model = config.ai_analytics.model
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml

logger = logging.getLogger(__name__)


class AIAnalyticsConfig:
    """AI Incident Analytics configuration section"""

    def __init__(self, config_dict: Dict[str, Any]):
        # Environment variables override config file
        self.enabled = self._get_bool("AI_ANALYTICS_ENABLED", config_dict.get("enabled", True))
        self.model = os.getenv("AI_ANALYTICS_MODEL") or config_dict.get("model", "sonnet")
        self.permission_mode = os.getenv("AI_ANALYTICS_PERMISSION_MODE") or config_dict.get("permission_mode", "default")

        # Setting sources
        setting_sources_str = os.getenv("AI_ANALYTICS_SETTING_SOURCES")
        if setting_sources_str:
            self.setting_sources = [s.strip() for s in setting_sources_str.split(",") if s.strip()]
        else:
            self.setting_sources = config_dict.get("setting_sources", ["project", "user"])

        # Allowed tools
        allowed_tools_str = os.getenv("AI_ANALYTICS_ALLOWED_TOOLS")
        if allowed_tools_str:
            self.allowed_tools = [t.strip() for t in allowed_tools_str.split(",") if t.strip()]
        else:
            self.allowed_tools = config_dict.get("allowed_tools", [
                "Bash(docker ps:*)",
                "Bash(docker top:*)",
                "Bash(docker stats:*)",
                "Bash(docker logs:*)",
                "Bash(docker inspect:*)",
                "Bash(docker exec:*)",
                "Read",
                "Grep",
                "Glob",
            ])

    def _get_bool(self, env_var: str, default: bool) -> bool:
        """Get boolean from environment or use default"""
        env_val = os.getenv(env_var)
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes")
        return default


# Offered in the model picker when the config file does not say otherwise.
DEFAULT_AVAILABLE_MODELS: List[Dict[str, str]] = [
    {
        "id": "claude-opus-5",
        "label": "Opus 5",
        "description": "Best judgment. Default for incident work.",
    },
    {
        "id": "claude-sonnet-5",
        "label": "Sonnet 5",
        "description": "Faster and cheaper. Good for routine questions.",
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Haiku 4.5",
        "description": "Fastest. Best for simple lookups.",
    },
]


class AgentConfig:
    """
    Interactive chat agent configuration section (YAML key: ai_agent).

    Separate from AIAnalyticsConfig, which covers the background PGMQ-driven
    incident analysis. Same precedence rule: environment beats YAML beats the
    defaults here.
    """

    def __init__(self, config_dict: Dict[str, Any]):
        self.model = os.getenv("AI_AGENT_MODEL") or config_dict.get("model", "claude-opus-5")
        self.permission_mode = os.getenv("AI_AGENT_PERMISSION_MODE") or config_dict.get("permission_mode", "default")

        # Ask the user before running a tool that isn't already allow-listed.
        self.require_tool_approval = self._get_bool(
            "AI_AGENT_REQUIRE_TOOL_APPROVAL", config_dict.get("require_tool_approval", True)
        )

        self.max_turns = self._get_int("AI_AGENT_MAX_TURNS", config_dict.get("max_turns", 10))
        self.max_budget_usd = self._get_float("AI_AGENT_MAX_BUDGET_USD", config_dict.get("max_budget_usd", 0)) or None

        # Drop the CLI subprocess after this long without traffic. The session id
        # is kept, so the next message resumes rather than starting over.
        self.idle_timeout_s = self._get_float("AI_AGENT_IDLE_TIMEOUT_S", config_dict.get("idle_timeout_s", 900))

        # How long a tool approval prompt waits for the user before denying.
        self.permission_timeout_s = self._get_float(
            "AI_AGENT_PERMISSION_TIMEOUT_S", config_dict.get("permission_timeout_s", 300)
        )

        # Ceiling on live CLI subprocesses in this process - one per active
        # session, so this bounds memory under many open tabs.
        self.max_concurrent_cli = self._get_int(
            "AI_AGENT_MAX_CONCURRENT_CLI", config_dict.get("max_concurrent_cli", 8)
        )

        self.system_prompt = config_dict.get("system_prompt") or None

        # Models a user may switch to from the UI. This is an allowlist, not a
        # suggestion: whatever arrives over the socket is checked against it
        # before reaching the CLI, so a crafted message cannot pick an
        # arbitrary model. The configured `model` is always included.
        self.available_models = self._load_models(config_dict)

    def _load_models(self, config_dict: Dict[str, Any]) -> List[Dict[str, str]]:
        env_models = os.getenv("AI_AGENT_AVAILABLE_MODELS")
        if env_models:
            entries: List[Dict[str, str]] = [
                {"id": m.strip(), "label": m.strip(), "description": ""}
                for m in env_models.split(",")
                if m.strip()
            ]
        else:
            entries = []
            for item in config_dict.get("available_models") or DEFAULT_AVAILABLE_MODELS:
                if isinstance(item, str):
                    entries.append({"id": item, "label": item, "description": ""})
                elif isinstance(item, dict) and item.get("id"):
                    entries.append({
                        "id": str(item["id"]),
                        "label": str(item.get("label") or item["id"]),
                        "description": str(item.get("description") or ""),
                    })

        # The active model must always be selectable, or the UI would show a
        # current value that cannot be chosen again after switching away.
        if not any(e["id"] == self.model for e in entries):
            entries.insert(0, {"id": self.model, "label": self.model, "description": ""})

        return entries

    def is_model_allowed(self, model: str) -> bool:
        return any(e["id"] == model for e in self.available_models)

    def _get_bool(self, env_var: str, default: bool) -> bool:
        env_val = os.getenv(env_var)
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes")
        return bool(default)

    def _get_int(self, env_var: str, default: Any) -> int:
        raw = os.getenv(env_var, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning(f"Invalid integer for {env_var}: {raw!r}; using {default}")
            return int(default)

    def _get_float(self, env_var: str, default: Any) -> float:
        raw = os.getenv(env_var, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning(f"Invalid number for {env_var}: {raw!r}; using {default}")
            return float(default)


class Config:
    """Central configuration object loaded from dev.config.yaml"""

    def __init__(self):
        # Core settings
        self.database_url: Optional[str] = None
        self.port: str = "8002"
        self.redis_url: Optional[str] = None

        # Supabase
        self.supabase_url: Optional[str] = None
        self.supabase_anon_key: Optional[str] = None
        self.supabase_service_role_key: Optional[str] = None
        self.supabase_jwt_secret: Optional[str] = None

        # External services
        self.anthropic_api_key: Optional[str] = None
        self.slack_bot_token: Optional[str] = None

        # AI Analytics
        self.ai_analytics: Optional[AIAnalyticsConfig] = None

        # Interactive chat agent
        self.agent: Optional[AgentConfig] = None

        # Load configuration
        self._load_config()

    def _find_config_file(self) -> Optional[Path]:
        """Find config file in search paths"""
        # First, check inres_CONFIG_PATH environment variable (used in Docker)
        env_config_path = os.getenv("inres_CONFIG_PATH")
        if env_config_path:
            config_path = Path(env_config_path)
            if config_path.exists():
                logger.info(f"  Found config file from inres_CONFIG_PATH: {config_path}")
                return config_path
            else:
                logger.warning(f"  inres_CONFIG_PATH set but file not found: {config_path}")

        # Fallback search paths
        search_paths = [
            Path("/app/config.yaml"),  # Docker mount point
            Path(__file__).parent.parent / "config" / "dev.config.yaml",  # api/config/dev.config.yaml
            Path(__file__).parent.parent / "cmd" / "server" / "dev.config.yaml",  # Legacy
            Path("/etc/inres/config.yaml"),  # System-wide
            Path.home() / ".inres" / "config.yaml",  # User
        ]

        for config_path in search_paths:
            if config_path.exists():
                logger.info(f"  Found config file: {config_path}")
                return config_path

        logger.warning("No config file found, using environment variables only")
        return None

    def _load_config(self):
        """Load configuration from file and environment variables"""
        config_dict = {}

        # Load from file
        config_file = self._find_config_file()
        if config_file:
            try:
                with open(config_file) as f:
                    config_dict = yaml.safe_load(f) or {}
                    logger.info(f"  Loaded config from: {config_file}")
            except Exception as e:
                logger.error(f"Failed to load config from {config_file}: {e}")

        # Core settings (env var overrides config file)
        self.database_url = os.getenv("DATABASE_URL") or config_dict.get("database_url")
        self.port = os.getenv("AI_PORT") or os.getenv("PORT") or config_dict.get("port", "8002")
        self.redis_url = os.getenv("REDIS_URL") or config_dict.get("redis_url")

        # Supabase
        self.supabase_url = os.getenv("SUPABASE_URL") or config_dict.get("supabase_url")
        self.supabase_anon_key = os.getenv("SUPABASE_ANON_KEY") or config_dict.get("supabase_anon_key")
        self.supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or config_dict.get("supabase_service_role_key")
        self.supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET") or config_dict.get("supabase_jwt_secret")

        # External services
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or config_dict.get("anthropic_api_key")
        self.slack_bot_token = os.getenv("SLACK_BOT_TOKEN") or config_dict.get("slack_bot_token")

        # AI Analytics
        ai_analytics_dict = config_dict.get("ai_incident_analytics", {})
        self.ai_analytics = AIAnalyticsConfig(ai_analytics_dict)

        # Interactive chat agent
        self.agent = AgentConfig(config_dict.get("ai_agent", {}))

        # Log what was loaded
        self._log_config()

    def _log_config(self):
        """Log loaded configuration (without secrets)"""
        logger.info("Configuration loaded:")
        logger.info(f"  - Database: {'OK' if self.database_url else 'MISSING'}")
        logger.info(f"  - Port: {self.port}")
        logger.info(f"  - Redis: {'OK' if self.redis_url else 'MISSING'}")
        logger.info(f"  - Supabase: {'OK' if self.supabase_url else 'MISSING'}")
        logger.info(f"  - Anthropic API: {'OK' if self.anthropic_api_key else 'MISSING'}")
        logger.info(f"  - AI Analytics: enabled={self.ai_analytics.enabled}, model={self.ai_analytics.model}")
        logger.info(
            f"  - Chat Agent: model={self.agent.model}, "
            f"tool_approval={self.agent.require_tool_approval}, "
            f"idle_timeout={self.agent.idle_timeout_s}s, "
            f"max_concurrent={self.agent.max_concurrent_cli}"
        )


# Global singleton instance
# Import this in other modules: from config import config
config = Config()
