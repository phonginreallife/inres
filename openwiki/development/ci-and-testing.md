---
type: operations
title: CI Pipelines and Testing
description: The five GitHub Actions workflows that gate InRes, how semantic-release drives container image tags, and where the focused tests for webhook normalization, authorization and agent stream translation live.
tags: [ci, github-actions, testing, semantic-release, docker, codeql, lint]
sources:
  - id: openwiki-source-1f5556594237f9562185646d
    resource: repo://.github/workflows/build.yaml
  - id: openwiki-source-7a33921cc1092566104466f8
    resource: repo://.github/workflows/codeql.yaml
  - id: openwiki-source-a5c30adf1d6d6d9a6b76ff83
    resource: repo://.github/workflows/lint.yaml
  - id: openwiki-source-f8b7b48444f96e08372841e6
    resource: repo://.github/workflows/release.yaml
  - id: openwiki-source-eb8db7eaad74c9ff88a54cd3
    resource: repo://.github/workflows/test.yaml
  - id: openwiki-source-809fc73d58cf3505702da4b6
    resource: repo://.releaserc.json
  - id: openwiki-source-d1a91edd2e4423eb88906c83
    resource: repo://server/agent/Makefile
  - id: openwiki-source-b4069dfea44d2d4aa1b70cd1
    resource: repo://server/agent/pytest.ini
  - id: openwiki-source-5124fcedf7b0938f58af1e14
    resource: repo://server/agent/tests/test_permissions.py
  - id: openwiki-source-1b3ba3af80313d11aae0b666
    resource: repo://server/agent/tests/test_session.py
  - id: openwiki-source-2b7ba9dd6019af15399e3a02
    resource: repo://server/agent/tests/test_translate.py
  - id: openwiki-source-2787848e41cd9bc979cf4797
    resource: repo://server/api/authz/authz_test.go
  - id: openwiki-source-cdf47924ec50053143ae314d
    resource: repo://server/api/authz/simple_test.go
  - id: openwiki-source-805afcbb4d85ed6f19fd899b
    resource: repo://server/api/go.mod
  - id: openwiki-source-98954dba5890b96e61f3ff01
    resource: repo://server/api/handlers/incident_test.go
  - id: openwiki-source-d97413c5a3806b1557cc01ef
    resource: repo://server/api/handlers/webhook_datadog_test.go
  - id: openwiki-source-ca06b610540efc25508579b6
    resource: repo://server/api/handlers/webhook_prometheus_test.go
generated: { by: "claude-code", at: "2026-09-17T11:09:25.960Z" }
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T11:09:25.960Z
---

# CI Pipelines and Testing

Five workflows run against the repository. Only one of them can actually block a
merge on test failures, which is worth knowing before relying on a green check.

| Workflow | Trigger | Gates |
|---|---|---|
| `test.yaml` | push/PR to `main` | Go tests (blocking); frontend and agent tests (non-blocking) |
| `lint.yaml` | push/PR to `main` | `go vet`, golangci-lint, ESLint, flake8 (blocking); black (non-blocking) |
| `build.yaml` | push/PR touching `server/**` or `frontend/**`, tags, manual | Builds and pushes four images |
| `release.yaml` | push to `main`, `v*` tags | semantic-release, then triggers `build.yaml` |
| `codeql.yaml` | push/PR to `main`, weekly cron | Security analysis for Go, JS/TS and Python |

Every workflow sets a `concurrency` group keyed on `github.ref`. All cancel
in-progress runs except `release.yaml`, which sets
`cancel-in-progress: false` — cancelling a release midway could publish a tag
without its artifacts.

Related: [Deployment](../operations/deployment.md) ·
[Architecture overview](../architecture/overview.md)

---

## `test.yaml`

Three parallel jobs, with meaningfully different strictness.

**`test-api`** is the only job that genuinely gates. It spins up a
`postgres:15` service container with a `pg_isready` health check and runs
`go test -v -race -coverprofile=coverage.out ./...` against it. The `-race` flag
matters here — the API runs background workers as goroutines, so data races are
a live risk. Coverage goes to Codecov with `fail_ci_if_error: false`.

**`test-frontend`** type-checks with `npx tsc --noEmit` and runs `npm test`, but
**both are marked `continue-on-error: true`**. Only `npm run build` can fail the
job. In practice the frontend gate is "it compiles", not "its tests pass".

**`test-agent`** installs requirements plus pytest and runs
`pytest --cov=. --cov-report=xml` — also `continue-on-error: true`. The agent's
test suite is the most rigorous in the repository, but CI does not currently
enforce it.

---

## `lint.yaml`

`go vet ./...` plus golangci-lint at `latest` for the API; `npm run lint`
(ESLint) for the frontend; and for the agent, `flake8` restricted to
`E9,F63,F7,F82` — syntax errors and undefined names, not style — with `black
--check` running non-blocking. The narrow flake8 selection means the Python gate
catches breakage, not formatting drift.

Each job declares `permissions: contents: read`, following least privilege.

---

## `build.yaml` and image tagging

The build workflow re-runs API and frontend tests itself, then builds four
images in a matrix with `fail-fast: false`:

| Image | Context | Dockerfile |
|---|---|---|
| `inres-api` | repo root | `server/api/Dockerfile` |
| `inres-agent` | `server/agent` | `server/agent/Dockerfile` |
| `inres-frontend` | `frontend/inres` | `frontend/inres/Dockerfile` |
| `inres-slack-worker` | `server/slack-worker` | `server/slack-worker/Dockerfile` |

The API build uses the **repository root** as context while the others use their
own directory, because the Go build needs files outside `server/api`. The
Cloudflare uptime worker is explicitly excluded — a comment notes it deploys via
wrangler, not Docker.

### Tag derivation

`docker/metadata-action` computes tags from the git ref:

- `type=semver,pattern={{version}}` → `v1.2.3` becomes `1.2.3`
- `type=semver,pattern={{major}}.{{minor}}` → `1.2`
- `type=semver,pattern={{major}}` → `1`, but **disabled for `v0.x` tags**,
  because a floating `0` tag across breaking pre-1.0 releases would be
  actively misleading
- `type=raw,value=latest` on the default branch only
- `type=ref` for tags and PRs

### Safety properties

- `push` is false for pull requests, so a PR builds but never publishes.
- The build job is skipped for PRs from forks
  (`github.event.pull_request.head.repo.full_name == github.repository`), which
  keeps fork PRs away from `packages: write`.
- GHCR login is skipped for pull requests entirely.
- `sbom: true` and `provenance: true` attach a software bill of materials and
  build provenance to every published image.
- GitHub Actions cache is scoped per matrix entry
  (`scope=${{ matrix.name }}`), so the four images do not evict each other.

---

## `release.yaml` and versioning

Releases are automated with **semantic-release 24** driven by `.releaserc.json`:

- Branches: `main`, plus `beta/*` publishing prereleases tagged `beta`.
- The Angular commit-analyzer preset, extended so that `refactor` and `perf`
  commits produce a patch release, and `docs(README)` does too.
- Release notes are generated, and the GitHub plugin runs with
  `failComment: false`.

Checkout uses `fetch-depth: 0` (semantic-release needs full history to compute
the next version) and `persist-credentials: false`.

### The handoff to builds

Release and build are separate workflows, so the release job exports
`new_release_published` and `new_release_version` as outputs and a second job
dispatches `build.yaml` at `v${version}` — but only when a release actually
happened. This explicit dispatch exists because a tag created by a workflow
using `GITHUB_TOKEN` does not trigger other workflows.

This handoff has been broken twice, and both fixes are preserved in the
workflow's comments. They are worth reading before changing it.

**The outputs were never set.** `npx semantic-release` writes nothing to
`$GITHUB_OUTPUT` — the `new_release_*` outputs come from
`cycjimmy/semantic-release-action`, which this workflow does not use. The
`trigger-builds` job was therefore evaluating `'' == 'true'` and **skipping on
every release**, so tagged images were never built. The step now derives the
outputs itself by comparing the newest `v*` tag before and after the
semantic-release run, which keeps semantic-release as the only dependency.

**The dispatch could silently do nothing.** `GITHUB_TOKEN` is restricted from
starting workflow runs in some repository configurations, and a dispatch that
is accepted but produces no run is exactly what left **v1.10.0 without images**.
Two guards now cover it: the step prefers a `RELEASE_PAT` secret when one is
configured, and after dispatching it waits 15 seconds, lists recent
`workflow_dispatch` runs of `build.yaml`, and **fails the job** unless a run for
that tag appears — telling you to configure `RELEASE_PAT` with `actions: write`.

The pattern generalises: a fire-and-forget dispatch that reports success
regardless is indistinguishable from a working one until someone goes looking
for the artifacts.

---

## `codeql.yaml`

Analyses all three languages in a matrix — Go with `autobuild`, JavaScript/
TypeScript and Python with `build-mode: none` — on push, PR, and a weekly cron
(Thursdays 21:15 UTC). It holds `security-events: write` to upload results.

---

## Test suites

### Go

Twelve test files, concentrated where correctness is hardest to eyeball:

- **Webhook normalization** — `webhook_prometheus_test.go`,
  `webhook_datadog_test.go`, `webhook_pagerduty_test.go`,
  `webhook_coralogix_test.go`. The Datadog file is the fullest example, covering
  payload processing, timestamp parsing, priority mapping and nested map
  extraction. These matter because each provider's payload shape is external and
  only a test pins the mapping. See
  [alert ingestion](../workflows/alert-ingestion.md).
- **Authorization** — five files in `authz/`. `authz_test.go` tests the
  permission matrices and role mapping as pure data with no database;
  `simple_test.go` tests the SQL authorizer against `go-sqlmock`. See
  [tenancy and authorization](../concepts/tenancy-and-authorization.md).
- **ReBAC at the handler boundary** — `incident_test.go` has
  `TestIncidentHandler_GetIncident_ReBAC`, checking enforcement where HTTP meets
  the service layer.
- **Config** — `config_test.go` pins environment-variable binding.
- **Identity** — `identity_test.go` covers the instance keypair.

`go-sqlmock` is a direct dependency, which is what lets service-layer tests run
without a live database.

### Python

`pytest.ini` sets `testpaths = tests` and `asyncio_mode = auto`, so async tests
need no per-test decorator. Three suites:

- **`test_translate.py`** — the largest, exercising the SDK-to-WebSocket
  translator against synthetic message sequences: duplicate-text reconciliation,
  subagent suppression, truncation, payload coercion. It can be exhaustive
  precisely because `translate()` is a pure function. See
  [streaming protocol](../ai-agent/streaming-protocol.md).
- **`test_permissions.py`** — the tool-approval broker, including the
  invariants the deadlock-freedom argument depends on.
- **`test_session.py`** — the full session lifecycle against an injected
  `FakeClient`, so no SDK or CLI subprocess is needed.

### Frontend

`scheduleTransformer.test.js` covers the schedule transformation layer — the
place where timeline correctness actually lives.

---

## Running tests locally

```bash
# Go — matches CI
cd server/api && go test -race ./...

# Python agent
cd server/agent && pytest          # or: make test

# Frontend
cd frontend/inres && npm test && npm run build

# Linting
cd server/api && go vet ./...
cd frontend/inres && npm run lint
cd server/agent && flake8 . --select=E9,F63,F7,F82
```

The agent's `Makefile` wraps the common development loop (`make dev` for uvicorn
with reload, `make test`, `make dev-docker`, `make logs`, `make clean`).

Because the frontend and agent jobs are `continue-on-error` in CI, running those
suites locally before pushing is the only reliable way to know they pass.
