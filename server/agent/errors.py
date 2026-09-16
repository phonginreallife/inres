"""
Error sanitisation shared by the WebSocket API and the session layer.

Full detail goes to the log; the client gets a generic message, so exception
text can't leak connection strings, tokens or internal paths.
"""

import logging

logger = logging.getLogger(__name__)


def sanitize_error_message(error: Exception, context: str = "") -> str:
    """
    Turn an exception into something safe to send to a browser.

    Args:
        error: The exception to sanitise.
        context: Where it happened, for the log line (e.g. "during agent turn").

    Returns:
        A generic message describing the class of failure.
    """
    logger.error(
        f"Error {context}: {type(error).__name__}: {str(error)}",
        exc_info=True,
    )

    text = str(error).lower()

    if isinstance(error, (ConnectionError, TimeoutError)):
        return "Service temporarily unavailable. Please try again."
    if isinstance(error, PermissionError):
        return "Access denied. Please check your permissions."
    if isinstance(error, ValueError):
        return "Invalid input provided. Please check your request."
    if "auth" in text or "token" in text:
        return "Authentication failed. Please verify your credentials."
    if "database" in text or "postgres" in text:
        return "Database error. Please contact support if this persists."
    return "An internal error occurred. Please contact support if this persists."
