## Symptom

Every audit hook invocation errors. The CLI reports it on each tool call, dumping a full minified JS stack:

```
Error in hook callback hook_1: ...
error: 'NoneType' object has no attribute 'put_nowait'
```

## Cause

`audit/service.py:344` leaves the queue unset until `start()` runs:

```python
self._queue: asyncio.Queue = None
```

and `log()` guards only the full-queue case:

```python
try:
    self._queue.put_nowait(event)
except asyncio.QueueFull:
    ...
```

Any caller that logs before `start()` gets an `AttributeError` raised straight back into it. The audit hooks are wired into the SDK, so the exception surfaces as a hook failure on every tool call.

## Impact

Audit is observability. Dropping an event when the service is not running is acceptable; raising into the agent's hook path is not - it produces alarming noise and risks masking real hook errors.

## Fix

Have `log()` no-op (with a warning, once) when `_queue` is None, or build the queue in `__init__` so it is always safe to call.

## Test

`tests/test_regressions.py::test_audit_log_before_start_does_not_raise` (currently `xfail(strict=True)`).
