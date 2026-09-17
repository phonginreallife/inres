"""
Tests for the tool-approval broker.

The interesting cases are the ones that would hang a real session: a decision
that never arrives, a decision for a request that already expired, and an
interrupt landing while a request is outstanding.
"""

import asyncio

import pytest

from session.permissions import PermissionBroker, matches_pattern


def broker(timeout_s=300.0, allowlist=None):
    queue: asyncio.Queue = asyncio.Queue()
    return PermissionBroker(queue, timeout_s=timeout_s, allowlist=allowlist), queue


def drain(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def wait_for_request(queue, timeout=1.0):
    """Wait until the broker has emitted its permission_request."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not queue.empty():
            return queue.get_nowait()
        await asyncio.sleep(0.005)
    raise AssertionError("no permission_request was emitted")


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


async def test_approve_allows_the_tool():
    b, q = broker()
    task = asyncio.create_task(b.can_use_tool("Bash", {"command": "ls"}))

    request = await wait_for_request(q)
    assert request["type"] == "permission_request"
    assert request["tool_name"] == "Bash"
    assert request["input_data"] == {"command": "ls"}
    assert request["tool_input"] == request["input_data"]

    assert b.resolve(request["request_id"], True) is True
    result = await task

    assert type(result).__name__ == "PermissionResultAllow"
    assert b.pending_count == 0


async def test_deny_blocks_the_tool_without_killing_the_turn():
    b, q = broker()
    task = asyncio.create_task(b.can_use_tool("Bash", {"command": "rm -rf /"}))

    request = await wait_for_request(q)
    b.resolve(request["request_id"], False)
    result = await task

    assert type(result).__name__ == "PermissionResultDeny"
    # interrupt=False lets the model respond in words instead of the turn dying.
    assert result.interrupt is False
    assert result.message


async def test_timeout_denies_and_emits_a_clearing_event():
    b, q = broker(timeout_s=0.05)

    result = await b.can_use_tool("Bash", {"command": "sleep 1"})

    assert type(result).__name__ == "PermissionResultDeny"
    assert result.interrupt is False
    kinds = [e["type"] for e in drain(q)]
    assert kinds == ["permission_request", "permission_timeout"]
    assert b.pending_count == 0


async def test_late_response_to_an_expired_request_is_rejected():
    b, q = broker(timeout_s=0.05)
    await b.can_use_tool("Bash", {"command": "x"})
    request = drain(q)[0]

    # The user clicks Approve after the request already timed out.
    assert b.resolve(request["request_id"], True) is False


async def test_unknown_request_id_is_rejected():
    b, _ = broker()
    assert b.resolve("does-not-exist", True) is False
    assert b.resolve(None, True) is False


async def test_double_resolve_is_rejected():
    b, q = broker()
    task = asyncio.create_task(b.can_use_tool("Read", {"file_path": "/etc/hosts"}))
    request = await wait_for_request(q)

    assert b.resolve(request["request_id"], True) is True
    assert b.resolve(request["request_id"], False) is False
    await task


# ---------------------------------------------------------------------------
# Shutdown and interrupt - the paths that would otherwise hang
# ---------------------------------------------------------------------------


async def test_deny_all_releases_waiters():
    b, q = broker()
    tasks = [
        asyncio.create_task(b.can_use_tool("Bash", {"command": "a"})),
        asyncio.create_task(b.can_use_tool("Bash", {"command": "b"})),
    ]
    await wait_for_request(q)
    await wait_for_request(q)
    assert b.pending_count == 2

    assert b.deny_all("Interrupted by user") == 2

    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)
    assert all(type(r).__name__ == "PermissionResultDeny" for r in results)
    assert b.pending_count == 0


async def test_deny_all_on_an_idle_broker_is_a_no_op():
    b, _ = broker()
    assert b.deny_all() == 0


async def test_close_releases_waiters_and_refuses_new_requests():
    b, q = broker()
    task = asyncio.create_task(b.can_use_tool("Bash", {"command": "a"}))
    await wait_for_request(q)

    b.close()
    assert type(await asyncio.wait_for(task, timeout=1.0)).__name__ == "PermissionResultDeny"

    # A request racing the close is denied immediately rather than parking.
    result = await asyncio.wait_for(b.can_use_tool("Bash", {"command": "b"}), timeout=1.0)
    assert type(result).__name__ == "PermissionResultDeny"


async def test_cancelling_the_waiter_does_not_leak_a_pending_entry():
    b, q = broker()
    task = asyncio.create_task(b.can_use_tool("Bash", {"command": "a"}))
    await wait_for_request(q)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert b.pending_count == 0


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


async def test_allowlisted_tool_skips_the_round_trip():
    b, q = broker(allowlist=["Read"])

    result = await asyncio.wait_for(b.can_use_tool("Read", {"file_path": "/tmp/x"}), timeout=1.0)

    assert type(result).__name__ == "PermissionResultAllow"
    assert drain(q) == []  # nothing was ever asked


@pytest.mark.parametrize(
    "pattern,tool,payload,expected",
    [
        ("Read", "Read", {"file_path": "/x"}, True),
        ("Read", "Write", {"file_path": "/x"}, False),
        ("Bash(kubectl get:*)", "Bash", {"command": "kubectl get pods"}, True),
        ("Bash(kubectl get:*)", "Bash", {"command": "kubectl delete pods"}, False),
        ("Bash(kubectl get:*)", "Read", {"command": "kubectl get pods"}, False),
        ("Bash(ls -la)", "Bash", {"command": "ls -la"}, True),
        ("Bash(ls -la)", "Bash", {"command": "ls -la /etc"}, False),
        ("Bash(*)", "Bash", {"command": "anything"}, True),
        ("Bash(docker logs*)", "Bash", {"command": "docker logs api"}, True),
        ("Grep(ERROR:*)", "Grep", {"pattern": "ERROR rate"}, True),
        ("", "Bash", {"command": "x"}, False),
        ("Bash(kubectl:*)", "Bash", {}, False),
    ],
)
def test_pattern_matching(pattern, tool, payload, expected):
    assert matches_pattern(pattern, tool, payload) is expected


def test_add_to_allowlist_is_idempotent():
    b, _ = broker(allowlist=["Read"])
    b.add_to_allowlist("Read")
    b.add_to_allowlist("Bash(ls:*)")

    assert b.is_allowed("Bash", {"command": "ls -la"}) is True
    assert b.is_allowed("Bash", {"command": "rm"}) is False


def test_non_dict_tool_input_does_not_crash_matching():
    assert matches_pattern("Bash(x:*)", "Bash", "not a dict") is False
