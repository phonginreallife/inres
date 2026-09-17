"""
Builders for SDK messages used across the tests.

``ResultMessage`` has six required fields, most of which no test cares about.
Constructing it through this helper keeps the tests readable and, more
importantly, keeps them honest: they build the same object the real SDK builds,
so a suite that passes locally against the shim also passes in the container
against the installed package.
"""

from session._sdk_types import ResultMessage


def result_message(
    subtype: str = "success",
    *,
    session_id: str = "claude-1",
    is_error: bool = False,
    num_turns: int = 1,
    duration_ms: int = 0,
    duration_api_ms: int = 0,
    **kwargs,
) -> ResultMessage:
    """A ResultMessage with the boilerplate filled in."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=duration_ms,
        duration_api_ms=duration_api_ms,
        is_error=is_error,
        num_turns=num_turns,
        session_id=session_id,
        **kwargs,
    )
