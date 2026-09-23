"""
End-to-end smoke ladder for the agent, against a real Claude Code CLI.

This is the ladder that isolated a production chat hang: each rung adds exactly
one of the options ``build_options`` sets, so a failure names the option that
broke rather than just saying "chat is down". Run it after any bump of
``claude-agent-sdk``, the bundled CLI, or the base image - those are precisely
the changes that move behaviour without moving our code.

Skipped by default. It spawns the CLI, spends tokens and needs credentials, so
it is opt-in:

    INRES_AGENT_SMOKE=1 pytest tests/test_agent_smoke.py -v

Requires ``CLAUDE_CODE_OAUTH_TOKEN`` (or ``ANTHROPIC_API_KEY``) and egress to
api.anthropic.com. Inside the cluster:

    kubectl -n inres exec deploy/inres-ai -- sh -c \\
      'cd /app && INRES_AGENT_SMOKE=1 python3 -m pytest tests/test_agent_smoke.py -v'

Two rungs are worth understanding before you read a failure:

* ``bypass_permissions`` fails as root with "--dangerously-skip-permissions
  cannot be used with root/sudo privileges". That is the container's normal
  user, so a failure there is expected until the linked issue is fixed - it is
  the reason the rung exists.
* ``full_config`` is the only rung that reads the deployed config.yaml. If every
  other rung passes and that one hangs, the fault is in configuration, not code.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("INRES_AGENT_SMOKE"),
    reason="live agent smoke test; set INRES_AGENT_SMOKE=1 to run",
)

MODEL = "claude-opus-5"
SIMPLE_PROMPT = "say hi"
TOOL_PROMPT = "Use your incident tools to list incidents from the last 24 hours."

# A turn is a couple of seconds when healthy. The generous ceiling is here to
# tell "hung" apart from "slow" - not to accommodate slowness.
TURN_TIMEOUT_S = 90


async def _drain(client, timeout=TURN_TIMEOUT_S):
    """Collect one turn's messages, or fail loudly on a hang."""
    seen = []

    async def pump():
        async for message in client.receive_response():
            seen.append(type(message).__name__)

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(
            f"turn produced no terminating ResultMessage within {timeout}s; "
            f"saw {seen or 'nothing at all'}"
        )
    return seen


async def _run(options, prompt=SIMPLE_PROMPT):
    from claude_agent_sdk import ClaudeSDKClient

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt=prompt)
        seen = await _drain(client)

    assert "ResultMessage" in seen, f"turn never completed; saw {seen}"
    assert "AssistantMessage" in seen, f"model produced no output; saw {seen}"
    return seen


def _workspace():
    """The agent's cwd, which is PVC-backed in the cluster."""
    from session.config import _workspace_for

    return _workspace_for(os.getenv("INRES_SMOKE_USER_ID", "smoke-user"))


# ---------------------------------------------------------------------------
# The ladder: one option per rung
# ---------------------------------------------------------------------------


async def test_streaming_bare():
    """Streaming stream-json I/O, nothing else. If this fails, nothing else matters."""
    from claude_agent_sdk import ClaudeAgentOptions

    await _run(ClaudeAgentOptions(model=MODEL, cwd=_workspace()))


async def test_partial_messages():
    """``include_partial_messages`` is what makes token streaming work in the UI."""
    from claude_agent_sdk import ClaudeAgentOptions

    await _run(
        ClaudeAgentOptions(model=MODEL, cwd=_workspace(), include_partial_messages=True)
    )


async def test_project_settings():
    """``setting_sources=["project"]`` makes the CLI read the workspace's .claude/."""
    from claude_agent_sdk import ClaudeAgentOptions

    await _run(
        ClaudeAgentOptions(
            model=MODEL,
            cwd=_workspace(),
            include_partial_messages=True,
            setting_sources=["project"],
        )
    )


async def test_in_process_mcp_server():
    """
    The incident tools are an in-process SDK MCP server sharing our event loop
    with the code awaiting the response, so a deadlock here is plausible enough
    to be worth a dedicated rung.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    from tools.incidents import create_incident_tools_server

    await _run(
        ClaudeAgentOptions(
            model=MODEL,
            cwd=_workspace(),
            include_partial_messages=True,
            setting_sources=["project"],
            mcp_servers={"incident_tools": create_incident_tools_server()},
        )
    )


@pytest.mark.xfail(strict=False, reason="gh issue: bypassPermissions is fatal as root")
async def test_bypass_permissions():
    """
    What ``require_tool_approval: false`` actually produces. Expected to fail as
    root - see the linked issue. Kept as a rung so the day it starts passing is
    visible.
    """
    from session.config import SessionConfig, build_options

    cfg = SessionConfig(user_id="smoke", session_id="smoke", require_tool_approval=False)
    await _run(build_options(cfg, can_use_tool=None, resume=None))


async def test_tool_call_with_approval():
    """
    The production shape: approval enabled, so the SDK routes every tool through
    ``can_use_tool``. Auto-approves, which is the one thing production does not
    do - a real user has to click - so this proves the machinery, not the UI.
    """
    from session.config import SessionConfig, build_options
    from session._sdk_types import PermissionResultAllow

    approved = []

    async def approve(tool_name, input_data, context):
        approved.append(tool_name)
        return PermissionResultAllow()

    cfg = SessionConfig(user_id="smoke", session_id="smoke")
    seen = await _run(build_options(cfg, can_use_tool=approve, resume=None), TOOL_PROMPT)

    assert approved, f"no tool was requested, so approval was never exercised; saw {seen}"


async def test_full_config_from_deployed_yaml():
    """
    The only rung that reads the deployed config.yaml rather than SessionConfig
    defaults. If every rung above passes and this one hangs, the fault is in
    configuration and the failure message below is the place to look.
    """
    from session.config import build_options
    from session._sdk_types import PermissionResultAllow
    from ws_chat import build_session_config

    async def approve(tool_name, input_data, context):
        return PermissionResultAllow()

    cfg = await build_session_config(
        os.getenv("INRES_SMOKE_USER_ID", "smoke-user"),
        "smoke",
        os.getenv("INRES_SMOKE_TOKEN", "smoke-token"),
        os.getenv("INRES_SMOKE_ORG_ID"),
        os.getenv("INRES_SMOKE_PROJECT_ID"),
    )
    await _run(build_options(cfg, can_use_tool=approve, resume=None), TOOL_PROMPT)
