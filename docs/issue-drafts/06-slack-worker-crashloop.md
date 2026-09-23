## Symptom

`inres-slack-worker` is in `CrashLoopBackOff`:

```
ERROR - Fatal error: Missing required configuration: slack_bot_token, slack_app_token
ValueError: Missing required configuration: slack_bot_token, slack_app_token
```

## Impact

Slack notifications do not work. Unrelated to the AI agent or to auth; noticed while debugging those.

## Fix

Either provide `slack_bot_token` and `slack_app_token` in the deployed config, or set the worker's replica count to 0 until Slack is configured, so a deliberate non-configuration does not present as a crash loop.
