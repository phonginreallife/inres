## Symptom

The AI service logs a Redis failure on every rate-limit check:

```
INFO:utils.redis_client:Connecting to Redis: redis://localhost:6379
ERROR:utils.redis_client:Redis connection failed: Error 111 connecting to localhost:6379. Connection refused.
ERROR:utils.redis_client:Rate limit check failed: ...
```

There is no Redis in the pod or the namespace.

## Cause

Two modules disagree about where the URL lives.

`config/settings.py:256` parses it from env **or** config.yaml:

```python
self.redis_url = os.getenv("REDIS_URL") or config_dict.get("redis_url")
```

`utils/redis_client.py:45` ignores that entirely and re-reads the env var with a localhost default:

```python
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
```

So `redis_url` set in config.yaml is parsed, logged as present at startup, and then never used.

## Impact

The limiter fails open by design (`redis_client.py:133`, "Fail open - allow request if Redis is down"), which is correct when Redis is genuinely down. The result here is that **`AI_RATE_LIMIT` enforces nothing at all** while appearing configured. Failing open because two modules disagree is a different thing from failing open because Redis is down.

## Fix

Have `get_redis()` read `config.redis_url`. Separately, decide whether a missing Redis should be a startup error when a rate limit is configured, so this cannot be silently inert again.

## Test

`tests/test_regressions.py::test_redis_client_uses_configured_url` (currently `xfail(strict=True)`).
