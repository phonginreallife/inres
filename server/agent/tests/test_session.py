"""
Tests for ChatSession's turn loop and lifecycle.

Driven by a fake SDK client that replays scripted message sequences, so these
exercise the real runner - connect, serve, disconnect, interrupt, resume -
without a CLI subprocess or an API key.

The property most worth protecting: **every path through a turn emits exactly
one terminal event** (``complete``, ``error`` or ``interrupted``). When that
fails, the browser spins forever with no way to recover but a reload.
"""

import asyncio

import pytest

from session._sdk_types import AssistantMessage, SystemMessage, TextBlock
from session.config import SessionConfig
from session.session import ChatSession, Turn
from tests.helpers import result_message


class FakeClient:
    """Stands in for ClaudeSDKClient, replaying one scripted response per turn."""

    def __init__(self, scripts, session_id="claude-1", resume=None, fail_connect=False):
        # Held by reference, not copied: a reconnect continues the conversation
        # from where the previous client left off.
        self._scripts = scripts
        self._session_id = session_id
        self.resume = resume
        self.fail_connect = fail_connect

        self.connected = False
        self.disconnected = False
        self.prompts = []
        self.interrupts = 0
        self._interrupted = False

    async def connect(self):
        if self.fail_connect:
            raise RuntimeError("cli unavailable")
        self.connected = True

    async def disconnect(self):
        self.disconnected = True
        self.connected = False

    async def query(self, prompt, session_id="default"):
        self.prompts.append(prompt)

    async def interrupt(self):
        self.interrupts += 1
        self._interrupted = True

    async def receive_response(self):
        script = self._scripts.pop(0) if self._scripts else []
        for message in script:
            if callable(message):
                message = await message()
            if self._interrupted:
                # The CLI discards whatever was in flight and reports a result,
                # so the iterator ends normally rather than raising.
                self._interrupted = False
                yield result_message(
                    "error_during_execution",
                    session_id=self._session_id,
                )
                return
            yield message


def reply(text, session_id="claude-1"):
    """A normal, complete turn."""
    return [
        SystemMessage(subtype="init", data={"session_id": session_id}),
        AssistantMessage(content=[TextBlock(text=text)], model="m"),
        result_message(session_id=session_id, total_cost_usd=0.01),
    ]


def make_session(scripts, **cfg_kwargs):
    """A session wired to a FakeClient, plus the queue and a handle on clients."""
    cfg = SessionConfig(
        user_id="u1",
        session_id="s1",
        require_tool_approval=cfg_kwargs.pop("require_tool_approval", False),
        idle_timeout_s=cfg_kwargs.pop("idle_timeout_s", 30.0),
        **cfg_kwargs,
    )
    queue: asyncio.Queue = asyncio.Queue()
    created = []

    def factory(config, can_use_tool, resume):
        client = FakeClient(scripts, resume=resume)
        created.append(client)
        return client

    return ChatSession(cfg, queue, client_factory=factory), queue, created


def drain(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def types_of(evs):
    return [e["type"] for e in evs]


TERMINAL = {"complete", "error", "interrupted"}


def terminals(evs):
    return [e["type"] for e in evs if e["type"] in TERMINAL]


async def run_turn(session, prompt="hello", timeout=2.0, **kwargs):
    turn = Turn(prompt=prompt, conversation_id="c1", **kwargs)
    await session.submit(turn)
    await asyncio.wait_for(turn.done.wait(), timeout=timeout)
    return turn


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_turn_streams_and_completes_once():
    session, queue, clients = make_session([reply("Hello there")])

    turn = await run_turn(session)
    await session.aclose()

    evs = drain(queue)
    assert terminals(evs) == ["complete"]
    assert "processing" in types_of(evs)
    assert turn.text == "Hello there"
    assert turn.error is None
    assert turn.interrupted is False
    assert turn.session_id == "claude-1"
    assert clients[0].prompts == ["hello"]


async def test_client_connects_lazily_and_disconnects_on_close():
    session, _, clients = make_session([reply("hi")])
    session.start()

    await asyncio.sleep(0.02)
    assert clients == []  # no message yet, no subprocess

    await run_turn(session)
    assert clients[0].connected is True

    await session.aclose()
    assert clients[0].disconnected is True


async def test_context_persists_across_turns_on_one_client():
    """The whole point of the refactor: one client, many turns."""
    session, _, clients = make_session([reply("first"), reply("second")])

    await run_turn(session, "one")
    await run_turn(session, "two")
    await session.aclose()

    assert len(clients) == 1
    assert clients[0].prompts == ["one", "two"]


async def test_turns_are_serialised_not_cancelled():
    """A second message must not cut the first turn short."""
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return AssistantMessage(content=[TextBlock(text="slow answer")], model="m")

    scripts = [
        [SystemMessage(subtype="init", data={"session_id": "claude-1"}), slow,
         result_message(session_id="claude-1")],
        reply("fast answer"),
    ]
    session, queue, _ = make_session(scripts)

    first = Turn(prompt="one", conversation_id="c1")
    second = Turn(prompt="two", conversation_id="c1")
    await session.submit(first)
    await asyncio.sleep(0.02)
    await session.submit(second)

    assert not first.done.is_set()
    gate.set()

    await asyncio.wait_for(first.done.wait(), timeout=2.0)
    await asyncio.wait_for(second.done.wait(), timeout=2.0)
    await session.aclose()

    assert first.text == "slow answer"
    assert second.text == "fast answer"
    assert terminals(drain(queue)) == ["complete", "complete"]


# ---------------------------------------------------------------------------
# Exactly one terminal event, on every path
# ---------------------------------------------------------------------------


async def test_connect_failure_reports_an_error_and_completes_the_turn():
    cfg = SessionConfig(user_id="u1", session_id="s1", require_tool_approval=False)
    queue: asyncio.Queue = asyncio.Queue()

    def factory(config, can_use_tool, resume):
        return FakeClient([], fail_connect=True)

    session = ChatSession(cfg, queue, client_factory=factory)

    turn = await run_turn(session)
    await session.aclose()

    assert terminals(drain(queue)) == ["error"]
    assert turn.error
    assert turn.done.is_set()


async def test_mid_turn_exception_becomes_a_single_error():
    class Exploding(FakeClient):
        async def receive_response(self):
            raise RuntimeError("stream died")
            yield  # pragma: no cover

    cfg = SessionConfig(user_id="u1", session_id="s1", require_tool_approval=False)
    queue: asyncio.Queue = asyncio.Queue()
    session = ChatSession(cfg, queue, client_factory=lambda c, t, r: Exploding([]))

    turn = await run_turn(session)
    await session.aclose()

    assert terminals(drain(queue)) == ["error"]
    assert turn.error


async def test_error_result_message_yields_an_error_not_a_complete():
    scripts = [[
        SystemMessage(subtype="init", data={"session_id": "claude-1"}),
        result_message("error_max_turns", session_id="claude-1", is_error=True,
                      result="hit the turn limit"),
    ]]
    session, queue, _ = make_session(scripts)

    turn = await run_turn(session)
    await session.aclose()

    assert terminals(drain(queue)) == ["error"]
    assert turn.error == "hit the turn limit"


async def test_empty_response_still_terminates():
    session, queue, _ = make_session([[]])

    turn = await run_turn(session)
    await session.aclose()

    assert terminals(drain(queue)) == ["complete"]
    assert turn.text == ""


# ---------------------------------------------------------------------------
# Interrupt
# ---------------------------------------------------------------------------


async def test_interrupt_stops_the_turn_and_keeps_partial_text():
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()
        return AssistantMessage(content=[TextBlock(text="never seen")], model="m")

    scripts = [[
        SystemMessage(subtype="init", data={"session_id": "claude-1"}),
        AssistantMessage(content=[TextBlock(text="partial answer")], model="m"),
        blocked,
        result_message(session_id="claude-1"),
    ]]
    session, queue, clients = make_session(scripts)

    turn = Turn(prompt="go", conversation_id="c1")
    await session.submit(turn)
    await asyncio.sleep(0.05)

    await session.interrupt()
    gate.set()

    await asyncio.wait_for(turn.done.wait(), timeout=2.0)
    await session.aclose()

    assert clients[0].interrupts == 1
    assert turn.interrupted is True
    assert turn.text == "partial answer"  # what the user actually read is kept
    assert terminals(drain(queue)) == ["interrupted"]


async def test_client_survives_an_interrupt_and_serves_the_next_turn():
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()
        return AssistantMessage(content=[TextBlock(text="x")], model="m")

    scripts = [
        [SystemMessage(subtype="init", data={"session_id": "claude-1"}), blocked,
         result_message(session_id="claude-1")],
        reply("second answer"),
    ]
    session, _, clients = make_session(scripts)

    turn = Turn(prompt="one", conversation_id="c1")
    await session.submit(turn)
    await asyncio.sleep(0.05)
    await session.interrupt()
    gate.set()
    await asyncio.wait_for(turn.done.wait(), timeout=2.0)

    second = await run_turn(session, "two")
    await session.aclose()

    assert second.text == "second answer"
    assert len(clients) == 1  # same subprocess, no reconnect


async def test_interrupt_while_idle_is_acknowledged_without_touching_the_client():
    session, queue, clients = make_session([reply("hi")])
    session.start()

    await session.interrupt()

    assert terminals(drain(queue)) == ["interrupted"]
    assert clients == []
    await session.aclose()


async def test_interrupt_releases_pending_approvals_first():
    """Otherwise the SDK reader is parked in can_use_tool and can't answer."""
    session, queue, _ = make_session([reply("hi")], require_tool_approval=True)
    approval = asyncio.create_task(session.broker.can_use_tool("Bash", {"command": "x"}))
    await asyncio.sleep(0.02)
    assert session.broker.pending_count == 1

    await session.interrupt()

    result = await asyncio.wait_for(approval, timeout=1.0)
    assert type(result).__name__ == "PermissionResultDeny"
    assert session.broker.pending_count == 0
    await session.aclose()


# ---------------------------------------------------------------------------
# Session identity, reset and resume
# ---------------------------------------------------------------------------


async def test_session_id_is_captured_for_resume():
    session, _, _ = make_session([reply("hi", session_id="claude-xyz")])

    turn = await run_turn(session)
    await session.aclose()

    assert session.claude_session_id == "claude-xyz"
    assert turn.session_id == "claude-xyz"


async def test_seeded_session_id_is_passed_as_resume():
    session, _, clients = make_session([reply("hi")])
    session.seed_session_id("claude-previous")

    await run_turn(session)
    await session.aclose()

    assert clients[0].resume == "claude-previous"


async def test_failed_resume_falls_back_to_a_fresh_session():
    """Session files are pod-local; losing history beats refusing the message."""
    cfg = SessionConfig(user_id="u1", session_id="s1", require_tool_approval=False)
    queue: asyncio.Queue = asyncio.Queue()
    created = []

    def factory(config, can_use_tool, resume):
        client = FakeClient([reply("fresh")], resume=resume, fail_connect=resume is not None)
        created.append(client)
        return client

    session = ChatSession(cfg, queue, client_factory=factory)
    session.seed_session_id("gone")

    turn = await run_turn(session)
    await session.aclose()

    assert [c.resume for c in created] == ["gone", None]
    assert turn.text == "fresh"
    assert terminals(drain(queue)) == ["complete"]


async def test_reset_drops_context_and_reconnects_without_resume():
    session, _, clients = make_session([reply("a"), reply("b")])

    await run_turn(session, "one")
    assert session.claude_session_id == "claude-1"

    await session.reset()
    await asyncio.sleep(0.05)
    assert session.claude_session_id is None

    await run_turn(session, "two")
    await session.aclose()

    assert len(clients) == 2
    assert clients[1].resume is None
    assert clients[0].disconnected is True


async def test_tenant_change_forces_a_reconnect():
    """Tool auth is bound at connect time, so a new org needs a new client."""
    session, _, clients = make_session([reply("a"), reply("b")])

    await run_turn(session, "one", org_id="org-a")
    await run_turn(session, "two", org_id="org-b")
    await session.aclose()

    assert len(clients) == 2
    assert clients[0].disconnected is True


async def test_same_tenant_reuses_the_client():
    session, _, clients = make_session([reply("a"), reply("b")])

    await run_turn(session, "one", org_id="org-a", project_id="p1")
    await run_turn(session, "two", org_id="org-a", project_id="p1")
    await session.aclose()

    assert len(clients) == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_idle_timeout_parks_the_subprocess_but_keeps_the_session_id():
    session, _, clients = make_session([reply("a"), reply("b")], idle_timeout_s=0.05)

    await run_turn(session, "one")
    await asyncio.sleep(0.2)

    assert clients[0].disconnected is True
    assert session.claude_session_id == "claude-1"

    await run_turn(session, "two")
    await session.aclose()

    assert len(clients) == 2
    assert clients[1].resume == "claude-1"  # conversation continues


async def test_aclose_is_idempotent():
    session, _, _ = make_session([reply("hi")])
    await run_turn(session)

    await session.aclose()
    await session.aclose()


async def test_aclose_without_any_turn_does_not_hang():
    session, _, clients = make_session([reply("hi")])
    session.start()

    await asyncio.wait_for(session.aclose(), timeout=2.0)
    assert clients == []


async def test_submit_restarts_a_dead_runner():
    session, _, clients = make_session([reply("a"), reply("b")])
    await run_turn(session, "one")

    session._force_restart()
    await asyncio.sleep(0.05)

    turn = await run_turn(session, "two")
    await session.aclose()

    assert turn.text == "b"
    assert clients[0].disconnected is True


async def test_concurrency_slot_is_released_when_connect_fails():
    """A leaked semaphore slot would eventually stall every new session."""
    from session import session as session_module

    session_module.configure_concurrency(1)

    cfg = SessionConfig(user_id="u1", session_id="s1", require_tool_approval=False)
    queue: asyncio.Queue = asyncio.Queue()
    failing = ChatSession(cfg, queue, client_factory=lambda c, t, r: FakeClient([], fail_connect=True))
    await run_turn(failing)
    await failing.aclose()

    ok_session, _, _ = make_session([reply("works")])
    turn = await run_turn(ok_session, timeout=2.0)
    await ok_session.aclose()

    assert turn.text == "works"

    session_module.configure_concurrency(8)


@pytest.mark.parametrize("require_approval", [True, False])
async def test_approval_setting_controls_whether_a_broker_exists(require_approval):
    session, _, _ = make_session([reply("hi")], require_tool_approval=require_approval)
    assert (session.broker is not None) is require_approval
    await session.aclose()
