"""Helpers for agent tools that call the InRes Go API."""

import logging
import os

logger = logging.getLogger(__name__)


def get_inres_api_base_url() -> str:
    """
    Base URL for the InRes Go API (incidents, releases, etc.).

    Docker Compose often sets ``inres_API_URL=http://host.docker.internal:8080``
    so a container can reach the API on the host. When the agent runs on the
    host itself, that hostname usually does not resolve; normalize to
    ``127.0.0.1`` unless the process appears to be inside a container.
    """
    raw = (
        os.getenv("inres_API_URL")
        or os.getenv("INRES_API_URL")
        or "http://127.0.0.1:8080"
    )
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raw = "http://127.0.0.1:8080"
    if "host.docker.internal" in raw and not os.path.exists("/.dockerenv"):
        resolved = raw.replace("host.docker.internal", "127.0.0.1")
        logger.info("Adjusted InRes API URL for host-run agent: %s -> %s", raw, resolved)
        return resolved
    return raw
