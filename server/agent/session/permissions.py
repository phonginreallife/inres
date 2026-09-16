"""
Tool approval.

The SDK calls ``can_use_tool`` before running a tool it isn't already permitted
to run. We turn that into a round trip through the browser: emit a
``permission_request``, park on a future, and let the WebSocket receive loop
resolve it when the user clicks Approve or Deny.

Why this is safe from deadlock
------------------------------
While a request is pending, the wait graph is::

    SDK reader task  -> the future
    session runner   -> the (empty) SDK message stream
    sender task      -> the (empty) output queue
    WS receive loop  -> the ASGI receive channel

Nothing the SDK owns feeds the ASGI channel, so no edge points back into the
receive loop, and the graph stays acyclic: the loop can always read the
``permission_response`` that breaks the cycle. Four things would break that
property, and the code is shaped to avoid all four:

1. **Never await an SDK control call from the receive loop while a request is
   pending.** ``interrupt()``, ``set_model()`` and ``disconnect()`` each await a
   control *response* that only the SDK's reader task can deliver - and that task
   may be parked inside this callback. ChatSession therefore calls
   :meth:`deny_all` *before* interrupting.
2. **Never await the turn task from the receive loop.** A turn blocked on a
   pending approval never finishes.
3. **Never bound the output queue.** A stalled sender would block the request
   before it was ever transmitted, so nobody could answer it.
4. **Never shield the future.** Cancellation has to propagate; orderly shutdown
   goes through :meth:`deny_all`.

:meth:`resolve` and :meth:`deny_all` are synchronous for the same reason - the
receive loop must never block while holding the only key.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional

from . import events
from ._sdk_types import PermissionResultAllow, PermissionResultDeny

logger = logging.getLogger(__name__)

# Which argument identifies what a tool is about to do, for allowlist matching.
# Falls back to the first string value in the input.
_PRIMARY_ARG = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "Grep": "pattern",
    "Glob": "pattern",
    "WebFetch": "url",
}


def _primary_argument(tool_name: str, input_data: Any) -> str:
    if not isinstance(input_data, dict):
        return ""

    key = _PRIMARY_ARG.get(tool_name)
    if key and isinstance(input_data.get(key), str):
        return input_data[key]

    for value in input_data.values():
        if isinstance(value, str):
            return value
    return ""


def matches_pattern(pattern: str, tool_name: str, input_data: Any) -> bool:
    """
    Test one allowlist entry.

    Entries come from the UI's "always allow" button and follow Claude Code's
    own syntax:

        ``Read``                 - any use of the tool
        ``Bash(kubectl get:*)``  - uses whose primary argument starts with the prefix
        ``Bash(ls -la)``         - one exact invocation
    """
    pattern = pattern.strip()
    if not pattern:
        return False

    if "(" not in pattern or not pattern.endswith(")"):
        return pattern == tool_name

    name, _, spec = pattern[:-1].partition("(")
    if name.strip() != tool_name:
        return False

    spec = spec.strip()
    argument = _primary_argument(tool_name, input_data)

    if spec in ("*", ":*"):
        return True
    if spec.endswith(":*"):
        return argument.startswith(spec[:-2])
    if spec.endswith("*"):
        return argument.startswith(spec[:-1])
    return argument == spec


class PermissionBroker:
    """Mediates tool approval between the SDK and the browser."""

    def __init__(
        self,
        out_queue: "asyncio.Queue[Optional[Dict[str, Any]]]",
        timeout_s: float = 300.0,
        allowlist: Optional[Iterable[str]] = None,
    ) -> None:
        self._out = out_queue
        self._timeout = timeout_s
        self._allowlist: List[str] = [p for p in (allowlist or []) if p]
        self._pending: Dict[str, "asyncio.Future[bool]"] = {}
        self._closed = False

    # -- state -------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return sum(1 for f in self._pending.values() if not f.done())

    def set_allowlist(self, patterns: Iterable[str]) -> None:
        self._allowlist = [p for p in patterns if p]

    def add_to_allowlist(self, pattern: str) -> None:
        if pattern and pattern not in self._allowlist:
            self._allowlist.append(pattern)

    def is_allowed(self, tool_name: str, input_data: Any) -> bool:
        return any(matches_pattern(p, tool_name, input_data) for p in self._allowlist)

    # -- called by the SDK, inside its own task ----------------------------

    async def can_use_tool(self, tool_name: str, input_data: Any, context: Any = None) -> Any:
        """
        Decide whether a tool may run, asking the user if we don't already know.

        Denials use ``interrupt=False`` so the model can explain itself or try
        another route; killing the turn outright leaves the user with nothing.
        """
        if self.is_allowed(tool_name, input_data):
            logger.debug("Tool %s pre-approved by allowlist", tool_name)
            return PermissionResultAllow()

        if self._closed:
            return PermissionResultDeny(message="Session is closing.", interrupt=False)

        request_id = uuid.uuid4().hex
        future: "asyncio.Future[bool]" = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        self._out.put_nowait(
            events.permission_request(
                request_id=request_id,
                tool_name=tool_name,
                input_data=input_data,
                suggestions=getattr(context, "suggestions", None),
            )
        )
        logger.info("Awaiting approval for %s (request %s)", tool_name, request_id)

        try:
            allowed = await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("Approval for %s timed out after %ss", tool_name, self._timeout)
            self._out.put_nowait(events.permission_timeout(request_id, tool_name))
            return PermissionResultDeny(
                message=(
                    f"No response from the user within {int(self._timeout)} seconds, "
                    "so this tool was not run. Continue without it or explain what you need."
                ),
                interrupt=False,
            )
        finally:
            self._pending.pop(request_id, None)

        if allowed:
            logger.info("Tool %s approved", tool_name)
            return PermissionResultAllow()

        logger.info("Tool %s denied", tool_name)
        return PermissionResultDeny(
            message="The user declined this tool call.",
            interrupt=False,
        )

    # -- called from the WebSocket receive loop; must not block -------------

    def resolve(self, request_id: Optional[str], allow: bool) -> bool:
        """
        Answer a pending request. Returns False if it is unknown or expired.
        """
        if not request_id:
            return False

        future = self._pending.get(request_id)
        if future is None or future.done():
            return False

        future.set_result(allow)
        return True

    def deny_all(self, reason: str = "Session closed") -> int:
        """
        Resolve every outstanding request as denied.

        Called before interrupting or closing. Denying rather than cancelling
        matters: a cancelled callback can leave the CLI waiting on a control
        response that never arrives, and ``disconnect()`` then hangs until its
        own timeout.
        """
        denied = 0
        for request_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(False)
                denied += 1
        if denied:
            logger.info("Denied %d pending permission request(s): %s", denied, reason)
        return denied

    def close(self) -> None:
        """Stop accepting new requests and release any outstanding ones."""
        self._closed = True
        self.deny_all("Session closed")
