---
type: operations
title: Configuration and Environment
description: How the Go API and the Python AI agent load one shared YAML file plus environment overrides, which settings are required versus optional, and what degrades when an optional dependency is missing.
tags: [configuration, environment, yaml, viper, deployment, redis, supabase]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-b677feaf4d6390ae5d7e5506
    resource: repo://deploy/docker/docker-compose.yaml
  - id: openwiki-source-e30d2632fc1566cc41240d4a
    resource: repo://deploy/docker/volumes/config/cfg.ex.yaml
  - id: openwiki-source-28c25db462e264c7cff861de
    resource: repo://server/agent/config/loader.py
  - id: openwiki-source-1dc7d84be02333cb492a4446
    resource: repo://server/agent/config/settings.py
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-0d58800e4a6061a65eeff1bb
    resource: repo://server/api/cmd/worker/main.go
  - id: openwiki-source-ad2bffcf07fa766623dc879c
    resource: repo://server/api/internal/config/config_test.go
  - id: openwiki-source-47096cf7b0e2deed96fdd235
    resource: repo://server/api/internal/config/config.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-2dbb67f8928bc8b46deadd33
    resource: repo://server/api/services/identity.go
  - id: openwiki-source-5f816c82203212b64e08ef56
    resource: repo://server/api/services/integration.go
  - id: openwiki-source-2c137197914f36eddffdeb14
    resource: repo://server/api/services/service.go
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Configuration and Environment

InRes runs two independently written services — a Go API and a Python agent —
that are deliberately configured from **one YAML file**. Docker Compose mounts
the same `dev.config.yaml` into both containers at `/app/config.yaml` and points
each at it with `inres_CONFIG_PATH`, so a deployment has a single file to edit
rather than one per language.

Both services follow the same precedence rule and both compensate for the same
legacy problem, but they implement it differently.

Related: [Architecture overview](../architecture/overview.md) ·
[Deployment](../operations/deployment.md) ·
[Authentication and identity](../concepts/authentication-and-identity.md)

---

## Precedence

**Environment beats YAML beats built-in defaults**, in both services.

### Go API

`config.LoadConfig` builds a Viper instance and layers four sources:

1. `godotenv.Load()` reads a `.env` file if present. A missing file is ignored
   on purpose — it is a local-development convenience so `go run` works without
   exporting variables manually, and in Docker or production there is none.
2. `v.SetDefault` supplies defaults: `port` → `8080`, `backend_url` →
   `http://localhost:8080`, `data_dir` → `./data`.
3. The config file. An explicit `path` argument wins; otherwise Viper searches
   `./config`, `./cmd/server` (legacy) and `.` for `dev.config.yaml`. A missing
   file is logged and tolerated — any *other* read error is returned as a fatal
   error, so a malformed file fails loudly rather than silently falling back.
4. Environment variables, via explicit `BindEnv` calls plus `AutomaticEnv`.

### Python agent

`config/settings.py` builds a `Config` singleton with the same rule expressed as
`os.getenv(...) or config_dict.get(...)` per field. Its file search is
`inres_CONFIG_PATH` first, then `/app/config.yaml`, then
`config/dev.config.yaml`, then `/etc/inres/config.yaml`, then
`~/.inres/config.yaml`. A file named by `inres_CONFIG_PATH` but missing logs a
warning and falls through to the search paths rather than failing.

---

## The environment backfill

Both services **write their resolved configuration back into the process
environment**, and for the same reason: a large amount of existing code reads
`os.Getenv` / `os.getenv` directly, and refactoring it all at once was not worth
doing.

- Go: `setEnvIfEmpty(key, value)` sets each variable **only when it is
  currently empty**, so an explicitly exported variable is never overwritten by
  a value from the YAML file. This preserves the precedence rule even for code
  that never sees the `Config` struct.
- Python: `config/loader.py` maps YAML keys onto environment variables through
  an explicit `env_mapping` table and assigns them unconditionally at import
  time, before the rest of the application loads.

A practical consequence, called out in the Compose file: the agent copies
`anthropic_api_key` from the YAML into the environment at startup, so that value
must be set in `dev.config.yaml` and not only in `.env`.

### Standard names, not prefixed ones

The Go service registers `SetEnvPrefix("inres")` for legacy support, but then
binds the **standard** names explicitly — `DATABASE_URL`, `REDIS_URL`, `PORT`,
`SUPABASE_URL`, `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN` and so on — so Docker and
Kubernetes deployments can use conventional variable names rather than
`inres_DATABASE_URL`. A few settings keep prefixed names because they have no
conventional equivalent: `inres_CLOUD_URL`, `inres_CLOUD_TOKEN` and
`inres_INSTANCE_ID` map onto the notification-gateway block.
`config_test.go` pins exactly this behaviour: standard variables land on the
struct, and `inres_CLOUD_URL` reaches the nested gateway config.

---

## Required settings

| Setting | Consumed by | Effect if missing |
|---|---|---|
| `database_url` | API, agent, workers | **Fatal.** The API server logs and exits; nothing works without Postgres |
| `supabase_url`, `supabase_anon_key` | API, frontend, agent | Authentication cannot complete |
| `supabase_service_role_key` | Backend operations, storage sync | Privileged reads/writes fail |
| `anthropic_api_key` | Agent, incident analytics | No AI features |

`supabase_jwt_secret` is **optional** and only needed for legacy HS256 tokens;
current projects use ES256/RS256 verified against the JWKS public key. See
[authentication and identity](../concepts/authentication-and-identity.md).

---

## Optional settings and graceful degradation

The system is built to start with pieces missing, and each omission has a
defined consequence rather than a crash.

**Redis is optional.** `cmd/server/main.go` tries the configured `redis_url`,
falls back to probing `localhost:6379`, and if neither answers logs
`Running without Redis - some features may be disabled` and continues with a
`nil` client. The agent's Redis client defaults to `redis://localhost:6379` and
backs the rate limiter and session store used for horizontal scaling.

**The notification gateway is optional.** It is only required for mobile push
notifications. `CloudRelayService.IsConfigured()` gates registration, and the
registration attempt itself runs in a background goroutine whose failure is
logged as a warning — a cloud relay that is down cannot delay or block API
startup.

**The identity service is optional at startup.** A failure to initialise logs a
warning and the router continues; the zero-trust device-certificate features are
simply unavailable.

**The analytics queue is best-effort.** `CreateQueueIfNotExists` failing logs a
warning rather than aborting router construction.

---

## The two AI configuration blocks

The YAML carries two distinct AI sections, and conflating them is the easy
mistake:

| Block | Drives | Env prefix |
|---|---|---|
| `ai_incident_analytics` | Background, PGMQ-driven incident analysis | `AI_ANALYTICS_*` (and `AI_PILOT_ENABLED` / `AI_PILOT_MODEL` on the Go side) |
| `ai_agent` | The interactive chat agent behind `/ws/chat` | `AI_AGENT_*` |

Both are read by the Python service into `AIAnalyticsConfig` and `AgentConfig`,
which apply the same environment-beats-YAML-beats-default rule field by field.
The Go service reads only `ai_incident_analytics`, since it owns the queue that
triggers the background analysis.

### Settings that bound resource use

`ai_agent` is where the agent's operational envelope is set, and several values
are load-bearing for stability:

- `max_concurrent_cli` (default 8) caps live CLI subprocesses — one per active
  chat session — so it bounds memory under many open tabs. The config comment
  warns to raise it only alongside the container's memory limit.
- `idle_timeout_s` (default 900) drops the CLI subprocess after silence while
  keeping the session id, so the next message resumes rather than starting over.
- `permission_timeout_s` (default 300) is how long a tool-approval prompt waits
  before being denied.
- `max_turns` (default 10) bounds agent turns per message;
  `max_budget_usd` is an optional hard spend cap per session, disabled at 0.
- `require_tool_approval` (default true) decides whether a `PermissionBroker`
  exists at all for a session.

Numeric parsing is defensive: `_get_int` and `_get_float` catch a malformed
environment value, log a warning naming the variable, and fall back to the
default instead of raising at import time.

### `available_models` is an allowlist, not a suggestion

`AgentConfig.available_models` is what the model picker offers **and** what
`is_model_allowed()` checks. Whatever arrives over the WebSocket is validated
against it before reaching the CLI, so a crafted message cannot select an
arbitrary model the credential could otherwise reach. Entries may be plain
strings or `{id, label, description}` objects, and `AI_AGENT_AVAILABLE_MODELS`
overrides them as a comma-separated list.

The configured `model` is **always inserted** if absent from the list, because
otherwise the UI would display a current value that could not be chosen again
after switching away.

---

## URL settings worth distinguishing

Several URL keys look interchangeable and are not:

- `supabase_url` — internal, for API→Supabase calls (a Docker service name).
- `public_supabase_url` — external, for the browser; when running under HTTPS
  this routes through nginx, which proxies `/auth`, `/rest`, `/realtime` and
  `/storage`.
- `mobile_supabase_url` — the address mobile clients are handed.
- `inres_api_url` — internal API URL used by the agent's incident tools.
- `backend_url` — the API the agent's zero-trust verifier calls to fetch
  instance public keys.
- `public_url` — the external API address given to mobile clients.
- `agent_url` — the AI agent service address.
- `webhook_api_base_url` — the base used when generating webhook URLs for
  integrations.

`data_dir` (default `./data`) is where the instance identity keypair is written,
as `identity.key`. It is not the only copy: `loadOrGenerateKey` resolves the key
**database first, then file, then generate**, syncing a file-loaded or freshly
generated key back to the `instance_identity` table. That ordering is what lets
a Kubernetes pod without a persistent volume keep a stable instance identity
across restarts — and a stable identity is what keeps previously issued device
certificates valid. `inres_INSTANCE_ID` selects which row is used, defaulting to
`default`.
