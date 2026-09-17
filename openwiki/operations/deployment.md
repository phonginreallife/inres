---
type: operations
title: Deployment and Operations
description: How InRes is packaged and run — the Kong gateway routing model, the Docker Compose stack, the Helm chart's per-component scaling and persistence, the migration ordering constraint, and the deployment CLI.
tags: [deployment, docker-compose, helm, kubernetes, kong, gateway, persistence, scaling]
sources:
  - id: openwiki-source-b677feaf4d6390ae5d7e5506
    resource: repo://deploy/docker/docker-compose.yaml
  - id: openwiki-source-3847fa5c659f0a1dcc0ec452
    resource: repo://deploy/docker/volumes/api/kong.yaml
  - id: openwiki-source-42eac6645e98d3814bca3479
    resource: repo://deploy/helm/inres/Chart.yaml
  - id: openwiki-source-0c65023b6743c2fc95457220
    resource: repo://deploy/helm/inres/templates/ai-clusterrole.yaml
  - id: openwiki-source-e7db220fa1f3ab8a3ed870d8
    resource: repo://deploy/helm/inres/templates/deployment.yaml
  - id: openwiki-source-cd41e395d77cca0775770256
    resource: repo://deploy/helm/inres/templates/hpa.yaml
  - id: openwiki-source-32a3bc7364bba55ede6ba90c
    resource: repo://deploy/helm/inres/templates/migration-job.yaml
  - id: openwiki-source-0f4ac35ea5330167c567f09e
    resource: repo://deploy/helm/inres/values.yaml
  - id: openwiki-source-12a35b326c8ff8be0ccf7424
    resource: repo://deploy/inres-cli/cmd/push.go
  - id: openwiki-source-d858e46ac35daba3443acc43
    resource: repo://deploy/inres-cli/cmd/root.go
  - id: openwiki-source-033d8799eca473af2f566528
    resource: repo://deploy/inres-cli/main.go
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-3959ed3b6b53a23e909f8e0f
    resource: repo://server/api/internal/background/incident.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
generated: { by: "claude-code", at: "2026-09-17T11:09:25.960Z" }
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T11:09:25.960Z
---

# Deployment and Operations

InRes ships as four container images plus a Cloudflare Worker, and can be run
either with Docker Compose or with the Helm chart. Both put **Kong in front** as
the single external entry point.

Related: [Configuration](../architecture/configuration.md) ·
[Data model and migrations](../operations/data-and-migrations.md) ·
[CI and testing](../development/ci-and-testing.md)

---

## Images

| Image | Built from |
|---|---|
| `ghcr.io/…/inres-api` | `server/api` (repo root as build context) |
| `ghcr.io/…/inres-agent` | `server/agent` |
| `ghcr.io/…/inres-frontend` | `frontend/inres` |
| `ghcr.io/…/inres-slack-worker` | `server/slack-worker` |

The uptime Worker is not containerised — it deploys through the Cloudflare API
(see [uptime monitoring](../monitoring/uptime-monitoring.md)). Tagging is
handled by CI; see [CI and testing](../development/ci-and-testing.md).

---

## The gateway routing model

`kong.yaml` is a **declarative** configuration (`KONG_DATABASE: "off"`), so
Kong has no datastore of its own. Routes are matched most-specific-first, with
the frontend catching everything left over:

| Path | Upstream | `strip_path` |
|---|---|---|
| `/api` | `api:8080` | **true** |
| `/ai/` | `agent:8002` | **true** |
| `/ws/chat` | `agent:8002` (ws) | false |
| `/ws/secure/chat` | `agent:8002` (ws) | false |
| `/webhook` | `api:8080` | **false** |
| `/` | `web:3000` | false |

Two `strip_path` decisions carry meaning.

`/api` **strips**, so `/api/incidents` reaches the Go service as `/incidents` —
which is why the router registers routes without an `/api` prefix, and why the
frontend's default base URL of `/api` works unchanged.

`/webhook` **does not strip**, because the Go router registers
`POST /webhook/:type/:integration_id` literally. An external monitoring system
posts to `https://host/webhook/prometheus/{id}` and the path arrives intact.

The WebSocket routes are declared with `protocol: ws` and `strip_path: false` so
the agent sees the full path it registered.

Every service carries the `cors` plugin. Kong is also configured with enlarged
proxy buffers (`160k`, `64 160k`), which matter for streamed agent responses.

---

## Docker Compose

`deploy/docker/docker-compose.yaml` runs redis, agent, api, slack-worker, web
and kong. Only Kong (8000), the API (8080) and the web app (3000) publish ports;
the rest use `expose` and are reachable only inside `inres-network`.

Postgres is **not** in the stack — the API joins an external Supabase network.
That network's name depends on `project_id` in `supabase/config.toml`, so the
compose file makes it overridable via `SUPABASE_NETWORK`, with comments
directing you to confirm the real names with `docker network ls` and
`docker ps`.

Two operational notes are documented inline:

- **`.env` lives next to the compose file**, not in your shell's working
  directory — Compose reads it from the directory containing the compose file.
- **Exactly one Anthropic credential.** An API key takes precedence wherever it
  is found, so using `CLAUDE_CODE_OAUTH_TOKEN` requires blanking
  `anthropic_api_key` in the config YAML too, since the agent copies that value
  into the environment at startup.

Redis runs with `--appendonly yes --maxmemory 256mb --maxmemory-policy
allkeys-lru` and a `redis-cli ping` healthcheck.

---

## Helm chart

`deploy/helm/inres` templates every workload from a single `components` map, so
adding a component is a values change rather than a new template.

### Replica resolution

The deployment template resolves replicas as
`components.<name>.replicas` → `components.<name>.replicaCount` → global
`replicaCount`, and the template comment explains why this is not a one-line
`default`:

1. `values.yaml` has always written `replicas` while the template read
   `replicaCount`, so **per-component counts were silently ignored** and every
   workload used the global value.
2. Helm's `default` treats `0` as empty, so `replicas: 0` would have become `1`
   and a component meant to be disabled **would have kept running**.

The guard uses `kindIs "invalid"` to distinguish "unset" from "set to zero".
That directly enables the `worker` component's `replicas: 0` default.

### Per-component configuration

| Component | Replicas | Service | Notes |
|---|---|---|---|
| `ai` | 1 | 8002 | 2 CPU / 2 Gi, two PVCs, RBAC enabled |
| `api` | global | 8080 | config secret + `emptyDir` at `/app/data` |
| `worker` | **0** | none | same image as api, `command: ["./worker"]` |
| `slack-worker` | global | none | poll interval, batch size, retries |
| `web` | global | 3000 | — |
| `kong` | global | 8000/8443/8001 | declarative config from a ConfigMap |

### Why the worker defaults to zero replicas

The values file explains this at length, and it is the chart's most
counter-intuitive default. `cmd/server` already starts the incident and
notification workers in-process, **so escalation runs today with zero worker
replicas**. The Deployment exists to move that work off the API pods when they
become CPU-bound.

Running both is safe rather than double-escalating, because the workers claim
rows with `FOR UPDATE SKIP LOCKED`. The worker ships in the API image — which
builds both binaries — so only the command differs.

### Persistence

The agent declares two PVCs, and they are not optional:

- **`/app/workspaces`** (10 Gi) — per-user workspaces holding synced memory,
  activated skills and cloned marketplaces. Without it, a restart wipes those
  files while their rows remain in Postgres, and the UI shows plugins as
  installed with their files gone. This is the same hazard the Compose stack's
  `agent_workspaces` volume addresses.
- **`/root/.claude`** (5 Gi) — Claude CLI state.

Both default to `ReadWriteOnce`, which constrains the agent to a single node
unless the storage class supports `ReadWriteMany` — worth knowing before raising
`ai.replicas`.

The API's `/app/data` is an `emptyDir`, which is safe because the instance
identity keypair is resolved database-first (see
[authentication and identity](../concepts/authentication-and-identity.md)).

### Configuration and secrets

Every component mounts `config.yaml` **read-only from the `inres-secrets`
Secret** rather than from a ConfigMap, and points `INRES_CONFIG_PATH` at it. The
same file reaches all services, matching the single-YAML design in
[configuration](../architecture/configuration.md). The migration job reads
`database-url`, `supabase-url` and `supabase-service-role-key` from the same
Secret.

### Migration ordering

The migration Job is a `pre-install,pre-upgrade` Helm hook with
`hook-weight: -5`, so it runs before any application pod and before other hooks.
`restartPolicy: Never` plus `backoffLimit` (default 3) bounds retries, and
`hook-delete-policy: before-hook-creation,hook-succeeded` cleans up. **A failed
migration blocks the release** rather than letting pods start against an
unmigrated schema.

It is `enabled: false` by default — deployments using Supabase-managed
migrations do not need it.

### Other chart features

**Autoscaling is per component**, and the deployment template omits the static
`replicas` field entirely when a component's autoscaling is enabled, so Helm
does not fight the HPA on every upgrade. The template records that this replaced
a single release-named HPA left over from `helm create` which **silently
targeted nothing**, since every workload is named `inres-<component>`.

The chart **hard-fails** if `components.ai.autoscaling` is enabled, with a
`fail` directive rather than a comment, for two reasons:

1. The agent's PVCs are `ReadWriteOnce`, so a second pod on another node cannot
   mount them.
2. Conversation resume reads the CLI transcript under `/root/.claude` **on the
   pod that created the session**. A request landing on any other pod resumes
   nothing and the conversation silently starts cold — the same pod-local
   transcript constraint that makes resume best-effort in
   [session architecture](../ai-agent/session-architecture.md).

Scaling the agent requires moving those volumes to `ReadWriteMany` and putting
session affinity in front of it first.

Optional templates cover Ingress, Gloo (for `/webhook/*` routing with TLS), a
ServiceAccount, and a ClusterRole/ClusterRoleBinding granting the agent pod read
access to Kubernetes resources — which is how the agent can inspect the cluster
during an incident.

### Image tags follow the chart

`image.tag` defaults to the **empty string**, and the deployment template
resolves it as component tag → global tag → `.Chart.AppVersion`. An empty
value therefore means "use the chart's `appVersion`", so **the chart version and
the images it deploys move together and cannot drift apart**. A deploy overrides
it with `--set image.tag=X`.

The comment is explicit that `latest` must never be used here: a rollback then
has nothing to roll back to, and two nodes pulling at different times can end up
running different code.

---

## The deployment CLI

`deploy/inres-cli` is a Cobra CLI with two commands.

`inres push [services...]` builds and pushes images for `web`, `api`, `ai` and
`slack-worker`, mapping each to its published name (note `ai` → `inres-agent`).
It takes `--registry`, `--tag`, `--platforms` and `--no-cache`, and builds the
Next.js app before its image.

`inres migrate` applies database migrations, covered in
[data model and migrations](../operations/data-and-migrations.md).
