## Symptom

Every incident question fails even when the agent responds. The model reports the backing API is unreachable:

```
UserMessage ToolResultBlock(content=[{'type': 'text', 'text': "Error: Network error o..."}])
AssistantMessage TextBlock(text="I couldn't retrieve the incidents - the backing API is unreachable.")
```

Reproduced by calling `mcp__incident_tools__get_incidents_by_time` directly from a probe inside `deploy/inres-ai`.

## Cause

`session/config.py::_sdk_env()` forwards `inres_API_URL` to the CLI subprocess only when it is already set in the AI pod's environment:

```python
for key in ("ANTHROPIC_API_KEY", "inres_API_URL", "inres_API_KEY"):
    value = os.getenv(key)
    if value:
        env[key] = value
```

The deployed AI container has no `inres_API_URL`. Its full env is `PORT`, `HOST`, `USER_WORKSPACES_DIR`, `inres_CONFIG_PATH`, `CLAUDE_CODE_OAUTH_TOKEN`. So the incident tools fall back to whatever default they carry, which does not resolve to `inres-api:8080` in the cluster.

## Impact

This is independent of the chat hang and will outlive fixing it. The agent's entire purpose is answering incident questions, and none of them can succeed until this is set.

## Fix

Add `inres_API_URL: "http://inres-api:8080"` to the `ai` component env in the chart (`deploy/helm/inres/values.yaml`), and have the tools fail loudly at startup when it is missing rather than at first use.
