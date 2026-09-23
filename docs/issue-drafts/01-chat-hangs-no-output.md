## Symptom

Sending any message in the AI Assistant shows `thinking...` forever. No answer, no error, no approval prompt. The user has no way to tell it failed.

Server side the turn is silent and only ends when the session is torn down:

```
INFO:session.session:Agent client connected for session ... (resume=None, model=claude-opus-5)
INFO:mcp.server.lowlevel.server:Processing request of type ListToolsRequest
  (167 seconds of nothing)
WARNING:session.session:Session runner did not stop in time; cancelling
INFO:session.session:Turn finished in 167.1s (tools=0, cost=None, interrupted=True)
```

`cost=None` means no `ResultMessage` ever arrived. `tools=0` plus zero `CallToolRequest` and zero `Awaiting approval` log lines means the model never emitted a tool call at all. The turn produced nothing whatsoever.

## Ruled out

Each verified by direct probe inside `deploy/inres-ai`:

| Suspect | Evidence it is not the cause |
| --- | --- |
| OAuth token | 108 bytes, `sk-ant-oat01-` prefix; CLI runs fine with it |
| Egress to Anthropic | `curl api.anthropic.com` returns 401 (reachable, unauthenticated) |
| Bundled CLI | `echo hi \| claude --print --model claude-opus-5` returns instantly, `EXIT=0` |
| Workspace cwd (PVC) | Same CLI command run from the workspace also returns instantly |
| `/root/.claude` volume | Present, writable, normal contents |
| Project settings | Workspace `.claude/` holds only an empty `CLAUDE.md` |
| Streaming stream-json | SDK probe returns `ResultMessage(subtype='success')` in ~1.3s |
| `include_partial_messages` | Same, StreamEvents flow normally |
| `setting_sources: ["project"]` | Same |
| In-process `incident_tools` MCP server | Same |
| Redis rate limiter | Fails open by design (`redis_client.py:133`) |
| Approval timeout | Never reached; `can_use_tool` is never called |
| OOM / restarts | 0 restarts, 212Mi of a 2Gi limit, 12m CPU while "hung" |

## The confusing part

A probe using the app's **own** `build_options()` with the full production option set (hooks + `can_use_tool` + MCP server + project settings + partial messages) **succeeds**:

```
ResultMessage(subtype='success', duration_ms=12119, num_turns=5)
can_use_tool called: get_current_time, get_incidents_by_time, get_incidents_by_time
```

So the options are not the cause. The remaining untested differences between that probe and production are:

1. The deployed `config.yaml` (the probe used `SessionConfig` defaults; production reads the file)
2. The uvicorn runtime - production runs on the app's shared event loop, the probe had its own

## Root cause (found)

It was never a hang. It was a **3-minute authentication retry with a spinner
over it.**

The deployed `config.yaml` (secret `inres-secrets`) carries an
`anthropic_api_key` that Anthropic rejects. `config/loader.py:72` copies it
into `os.environ["ANTHROPIC_API_KEY"]` when `claude_agent_api_v1` is imported,
and the Claude Code CLI prefers an API key over `CLAUDE_CODE_OAUTH_TOKEN`. So
the valid OAuth token in the pod was shadowed by an invalid key, the CLI got
`401 API key is invalid`, and retried with backoff for roughly 180 seconds
before giving up:

```
Agent client connected for session ... (resume=None, model=claude-opus-5)
Processing request of type ListToolsRequest
  ... 181 seconds ...
Turn finished in 181.2s (tools=0, cost=0, interrupted=False)
Live model switch to claude-sonnet-5 failed (Authentication failed. Please check your API credentials.)
```

Every earlier attempt was torn down by the user at ~167s - about 14 seconds
before the error would have surfaced - which is why it presented as infinite.
Once a turn was left alone the UI showed the real message:
"Failed to authenticate. API Error: 401 API key is invalid."

### Why every probe passed

The probes imported `ws_chat` and `session.config`, never
`claude_agent_api_v1`, so `load_config()`'s environment export never ran in
them. They inherited only the container env - OAuth token present, no API key
- and authenticated fine. The app process had both, and the key won. The one
variable that differed was invisible from outside the process.

`_sdk_env()`'s own docstring documents this trap; the runbook has a check for
it. Nothing enforced it.

## Fix

Deployment: remove `anthropic_api_key` from the config.yaml in `inres-secrets`
(or make it valid) and restart `inres-ai`.

Code (this repo):
- `_sdk_env()` warns loudly when both credentials are present, naming which
  one the CLI will use.
- startup verifies a configured API key with one cheap request and logs a
  CRITICAL line on 401 - the failure that took hours to find becomes one log
  line at boot.
- regression test for the both-credentials warning.

The "silent turn has no deadline" point below is now the more important
follow-up: 180 seconds of "thinking..." over a 401 is the UX that hid this.

## Where to pick up

Run the `full_config` rung of `tests/test_agent_smoke.py`, which is the only one that reads the deployed config.yaml. If it hangs, the fault is configuration. If it passes, the fault is in the session/WebSocket layer and the next step is instrumenting `_run_turn` in production.

## Related

A turn that yields nothing should surface an error rather than spinning forever. `idle_timeout_s` bounds silence *between* turns, not *within* one. That is the reason this read as a hang instead of a failure, and it should be fixed regardless of the root cause.
