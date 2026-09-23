## Symptom

`config/loader.py` logs the first 30 characters of every value it exports from
config.yaml into the environment, at INFO:

```python
logger.info(f"[config_loader] Set {env_key}={str(config[config_key])[:30]}...")
```

The exported list is almost entirely secrets: `ANTHROPIC_API_KEY`,
`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SLACK_BOT_TOKEN`,
`SLACK_APP_TOKEN`, `inres_API_KEY`.

## Impact

Thirty characters of an API key or a JWT signing secret is not a redaction.
Anyone with pod log access (kubectl, the log pipeline, its retention) gets a
substantial prefix of every credential the agent holds. For short secrets it
may be the entire value.

## Fix

Log the variable name and length only. Done in this branch alongside the
credential-shadowing fix; this issue exists so the change is reviewed on its
own merits and so log retention for existing pods can be considered.
