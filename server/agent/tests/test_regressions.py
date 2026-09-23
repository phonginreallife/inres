"""
Regression tests for defects found while debugging a production chat hang.

Open defects are marked ``xfail(strict=True)``: the suite stays green while
the bug is open, and the moment someone fixes it the test XPASSes and fails
the build, which is the prompt to delete the marker. A plain skip would let a
fix land silently and a plain failure would leave the suite red for as long as
the issue is open.

The credential tests at the bottom are different: they guard a fix that has
landed, so they are ordinary tests with no marker.

These are unit tests on purpose - no cluster, no CLI, no network. The live
end-to-end ladder that originally found these lives in ``test_agent_smoke.py``.
"""

import asyncio
import os

import pytest

from session.config import SessionConfig, build_options


# ---------------------------------------------------------------------------
# bypassPermissions is fatal when the agent runs as root
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="gh issue: bypassPermissions is fatal as root")
def test_build_options_does_not_bypass_permissions_as_root(monkeypatch):
    """
    With approval disabled, build_options falls back to bypassPermissions - and
    the CLI refuses that outright when euid is 0:

        --dangerously-skip-permissions cannot be used with root/sudo privileges

    The container runs as root, so ``require_tool_approval: false`` does not
    relax approval, it takes the agent down with exit code 1 on the first
    message. The fallback has to notice it is root and do something the CLI
    will actually accept.
    """
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)

    cfg = SessionConfig(user_id="u", session_id="s", require_tool_approval=False)
    opts = build_options(cfg, can_use_tool=None, resume=None)

    assert opts.permission_mode != "bypassPermissions", (
        "running as root, so the CLI will reject bypassPermissions and exit 1"
    )


# ---------------------------------------------------------------------------
# The audit service raises if a hook fires before start()
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="gh issue: AuditService.log() assumes start() ran")
async def test_audit_log_before_start_does_not_raise():
    """
    ``AuditService._queue`` is None until ``start()`` builds it, and ``log()``
    guards only ``asyncio.QueueFull``. Any caller that logs first gets
    ``AttributeError: 'NoneType' object has no attribute 'put_nowait'``.

    That is not hypothetical: it fires on every audit hook invocation in a
    process that has not started the service, which the CLI surfaces as
    "Error in hook callback" on every single tool call. Audit is observability -
    losing an event is acceptable, raising into the caller is not.
    """
    from audit.service import AuditEvent, AuditService

    service = AuditService()
    assert service.enabled, "log() short-circuits when disabled, which would void this test"
    assert service._queue is None, "start() must not have run, or there is no bug to catch"

    event = AuditEvent(
        event_type="test", user_id="u", action="probe", status="success"
    )

    await service.log(event)  # must not raise


# ---------------------------------------------------------------------------
# The configured Redis URL is parsed and then ignored
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="gh issue: redis_client ignores config.redis_url")
async def test_redis_client_uses_configured_url(monkeypatch):
    """
    ``settings.py`` reads ``redis_url`` from env *or* config.yaml, but
    ``utils/redis_client.py`` re-reads ``os.getenv("REDIS_URL")`` on its own and
    defaults to localhost. Set it in config.yaml and the client never sees it,
    so it dials localhost, fails, and the rate limiter fails open - which means
    ``AI_RATE_LIMIT`` silently enforces nothing.

    Failing open is the right call when Redis is genuinely down. Failing open
    because two modules disagree about where the URL lives is not.
    """
    import utils.redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)

    from config import settings as settings_mod

    monkeypatch.setattr(
        settings_mod.config, "redis_url", "redis://configured-host:6379", raising=False
    )

    seen = {}

    def fake_from_url(url, **kwargs):
        seen["url"] = url

        class _Stub:
            async def ping(self):
                return True

        return _Stub()

    monkeypatch.setattr(rc.redis, "from_url", fake_from_url)
    monkeypatch.setattr(rc, "_redis_pool", None, raising=False)

    await rc.get_redis()

    assert seen["url"] == "redis://configured-host:6379"


# A fourth defect - a turn that yields nothing spins until the session is torn
# down, showing "thinking..." with no error - has no test here on purpose. The
# fix has to introduce a turn-level output deadline first; until that API
# exists there is nothing to assert against, and an xfail built on
# ``pytest.fail`` could never XPASS, so it would never tell anyone it was fixed.
# It is tracked in the linked issue instead.


# ---------------------------------------------------------------------------
# Two credentials, one silent winner
# ---------------------------------------------------------------------------
#
# Not xfail: fixed, and these guard the fix. This was the root cause of the
# chat "hang". config.yaml carried an invalid anthropic_api_key, the loader
# exported it, and the CLI preferred it over a valid CLAUDE_CODE_OAUTH_TOKEN -
# then retried the 401 for three minutes under a spinner. Nothing said so.


def test_describe_credentials_names_the_shadowing():
    from session.config import describe_credentials

    msg = describe_credentials({"ANTHROPIC_API_KEY": "sk-x", "CLAUDE_CODE_OAUTH_TOKEN": "oat"})
    assert msg is not None
    assert "API key" in msg and "OAuth" in msg, "must say which one wins and which is ignored"
    assert "loader" in msg, "must point at where the key comes from, or nobody finds it"


def test_describe_credentials_is_quiet_when_unambiguous():
    from session.config import describe_credentials

    assert describe_credentials({"CLAUDE_CODE_OAUTH_TOKEN": "oat"}) is None
    assert describe_credentials({"ANTHROPIC_API_KEY": "sk-x"}) is None


def test_describe_credentials_flags_nothing_configured():
    from session.config import describe_credentials

    msg = describe_credentials({})
    assert msg is not None and "Neither" in msg


def test_sdk_env_warns_once_when_both_are_set(monkeypatch, caplog):
    """The warning must reach the log at connect time, and must not spam every turn."""
    import logging

    import session.config as sc

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat")
    monkeypatch.setattr(sc, "_credentials_warned", False)

    with caplog.at_level(logging.WARNING, logger="session.config"):
        sc._sdk_env()
        sc._sdk_env()

    hits = [r for r in caplog.records if "Credential check" in r.getMessage()]
    assert len(hits) == 1, f"expected exactly one warning, got {len(hits)}"


async def test_verify_api_key_is_a_noop_without_a_key(monkeypatch):
    from session.config import verify_api_key_at_startup

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert await verify_api_key_at_startup() is None
