## Symptom

Setting `ai_agent.require_tool_approval: false` does not relax tool approval, it kills the agent on the first message:

```
Tool approval is disabled; running with permission_mode=bypassPermissions
--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons
Fatal error in message reader: Command failed with exit code 1
ProcessError: Command failed with exit code 1
```

## Cause

`session/config.py:150-157` falls back to `bypassPermissions` when there is no `can_use_tool` callback:

```python
if can_use_tool is None and permission_mode == "default":
    permission_mode = "bypassPermissions"
```

The container runs as root, and the CLI refuses `--dangerously-skip-permissions` under uid 0. The fallback is therefore unusable in every deployed configuration.

## Impact

Latent today because approval is enabled in production, but it means the documented off switch is a trap: turning approval off takes the whole agent down with an error that does not mention approval.

## Fix

Detect uid 0 and either refuse the configuration at startup with a clear message, or run the CLI as a non-root user so `bypassPermissions` is actually available.

## Test

`tests/test_regressions.py::test_build_options_does_not_bypass_permissions_as_root` (currently `xfail(strict=True)`), plus the `bypass_permissions` rung of `tests/test_agent_smoke.py`.
