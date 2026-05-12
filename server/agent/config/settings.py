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


def _bootstrap_env_from_dotenv_files() -> None:
    """Load server/agent/.env then .env.dev so local secrets apply before Config reads os.environ."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).parent.parent
    load_dotenv(root / ".env")
    load_dotenv(root / ".env.dev", override=True)


_bootstrap_env_from_dotenv_files()


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

        # Fallback search paths (agent package root = parent of config/)
        agent_root = Path(__file__).parent.parent
        search_paths = [
            Path("/app/config.yaml"),  # Docker mount point (see docker-compose.dev.yaml)
            agent_root / "config.dev.yaml",  # Local dev defaults when running uvicorn on the host
            agent_root / "config" / "dev.config.yaml",
            agent_root / "cmd" / "server" / "dev.config.yaml",  # Legacy
            Path("/etc/inres/config.yaml"),  # System-wide
            Path.home() / ".inres" / "config.yaml",  # User
        ]

        for config_path in search_paths:
            if config_path.exists():
                logger.info(f"  Found config file: {config_path}")
                return config_path

        logger.warning("No config file found, using environment variables only")
        return None

    def _apply_host_dev_url_rewrites(self) -> None:
        """
        config.dev.yaml targets Docker service names / host.docker.internal.
        When uvicorn runs on the host, rewrite to loopback so Postgres / JWKS / Redis work.
        """
        if Path("/.dockerenv").exists():
            return

        def rewrite_docker_internal(val: Optional[str]) -> Optional[str]:
            if val and "host.docker.internal" in val:
                return val.replace("host.docker.internal", "127.0.0.1")
            return val

        self.database_url = rewrite_docker_internal(self.database_url)
        self.supabase_url = rewrite_docker_internal(self.supabase_url)
        self.redis_url = rewrite_docker_internal(self.redis_url)
        if self.redis_url and self.redis_url.startswith("redis://redis:"):
            self.redis_url = self.redis_url.replace("redis://redis:", "redis://127.0.0.1:", 1)

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

        self._apply_host_dev_url_rewrites()

        # External services (YAML often has anthropic_api_key: "" — treat blank as unset)
        self.anthropic_api_key = (
            (os.getenv("ANTHROPIC_API_KEY") or "").strip()
            or (str(config_dict.get("anthropic_api_key") or "").strip() or None)
        )
        self.slack_bot_token = (
            (os.getenv("SLACK_BOT_TOKEN") or "").strip()
            or (str(config_dict.get("slack_bot_token") or "").strip() or None)
        )

        # AI Analytics
        ai_analytics_dict = config_dict.get("ai_incident_analytics", {})
        self.ai_analytics = AIAnalyticsConfig(ai_analytics_dict)

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


# Global singleton instance
# Import this in other modules: from config import config
config = Config()
