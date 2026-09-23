import os
import yaml
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def load_config():
    """
    Load configuration from YAML file specified by inres_CONFIG_PATH.
    If file exists, load it and set environment variables for compatibility.

    Priority:
    1. inres_CONFIG_PATH env var
    2. /app/config/config.yaml (production)
    3. ./config.dev.yaml (local development)
    """
    config_path = os.getenv("inres_CONFIG_PATH")
    if not config_path:
        # Check default locations
        default_paths = [
            "/app/config/config.yaml",  # Production (Docker)
            os.path.join(os.path.dirname(__file__), "..", "config.dev.yaml"),  # Local dev (api/config.dev.yaml)
        ]
        for path in default_paths:
            if os.path.exists(path):
                config_path = path
                break

        if not config_path:
            logger.info("No config file found, skipping config file load")
            return

    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            
        if not config:
            logger.warning(f"Config file {config_path} is empty")
            return

        logger.info(f"  Loaded config from {config_path}")
        
        # Map config keys to environment variables
        # This allows existing code using os.getenv to work without changes
        env_mapping = {
            "database_url": "DATABASE_URL",

            # API Connections
            "inres_api_url": "inres_API_URL",
            "backend_url": "inres_BACKEND_URL",
            "inres_api_key": "inres_API_KEY",

            # Supabase
            "supabase_url": "SUPABASE_URL",
            "supabase_anon_key": "SUPABASE_ANON_KEY",
            "supabase_service_role_key": "SUPABASE_SERVICE_ROLE_KEY",
            "supabase_jwt_secret": "SUPABASE_JWT_SECRET",

            # External APIs
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "slack_bot_token": "SLACK_BOT_TOKEN",
            "slack_app_token": "SLACK_APP_TOKEN",

            # AI Agent Security
            "ai_allowed_origins": "AI_ALLOWED_ORIGINS",
            "ai_rate_limit": "AI_RATE_LIMIT",
        }
        
        for config_key, env_key in env_mapping.items():
            if config_key in config and config[config_key]:
                os.environ[env_key] = str(config[config_key])
                # Name and length only. This list is mostly secrets, and the
                # first 30 characters of an API key or JWT secret is not a
                # redaction - it went straight into the pod logs.
                logger.info(
                    f"[config_loader] Set {env_key} (len={len(str(config[config_key]))})"
                )

    except Exception as e:
        logger.error(f"Failed to load config file: {e}")
