---
type: operations
title: Data Model and Migrations
description: How the Postgres/Supabase schema is owned and evolved - the timestamp-ordered migration convention, the three ways migrations are applied, the PGMQ queues and their producers and consumers, and the RLS policies that enforce organization isolation.
tags: [database, postgres, supabase, migrations, pgmq, rls, tenant-isolation]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T11:09:25.960Z
sources:
  - id: openwiki-source-32a3bc7364bba55ede6ba90c
    resource: repo://deploy/helm/inres/templates/migration-job.yaml
  - id: openwiki-source-e14f131923ba3d8043c55c41
    resource: repo://deploy/inres-cli/cmd/migrate.go
  - id: openwiki-source-ba88407e754f311ac8472613
    resource: repo://server/agent/services/analytics.py
  - id: openwiki-source-b7ac60bca81274f953f09753
    resource: repo://server/agent/tools/incidents.py
  - id: openwiki-source-349b953ef4310fbbf38c78ea
    resource: repo://server/api/authz/simple.go
  - id: openwiki-source-d4b6a8dc3fb9ad15450b0f80
    resource: repo://server/api/cmd/migrate/main.go
  - id: openwiki-source-9bd2c63098e131340a8ca161
    resource: repo://server/api/db/db.go
  - id: openwiki-source-3b6b6b719c0d61055a052fb5
    resource: repo://server/api/internal/background/notification.go
  - id: openwiki-source-818fc8b55b30c170fb7e69b9
    resource: repo://server/api/services/incident_analytics_service.go
  - id: openwiki-source-9b6dbc33d6da6ba8d48fc24e
    resource: repo://server/api/services/incident.go
  - id: openwiki-source-7fa4739d35e8934014bbb4c3
    resource: repo://server/slack-worker/slack_repository.py
  - id: openwiki-source-95cb700da6b5ccaddcf27b7a
    resource: repo://server/slack-worker/slack_worker.py
  - id: openwiki-source-a5a98e2871efd1364da9c533
    resource: repo://supabase/migrations/20250925091841_remote_schema.sql
  - id: openwiki-source-2f8440a55eda93f830aa9e31
    resource: repo://supabase/migrations/20250926160648_queue_initial_data.sql
  - id: openwiki-source-2496dff3225cab4e508a0f60
    resource: repo://supabase/migrations/20251017163508_create_effective_shifts_view.sql
  - id: openwiki-source-b0760d248cd6f4f0eb13d16c
    resource: repo://supabase/migrations/20251125144121_create_incident_action_queue.sql
  - id: openwiki-source-c5408de9516b7b3ca7ed3217
    resource: repo://supabase/migrations/20251127150000_add_fulltext_search_to_incidents.sql
  - id: openwiki-source-444ee55fa4f53e95c8b48f0a
    resource: repo://supabase/migrations/20251202000000_add_org_project_memberships.sql
  - id: openwiki-source-a0553076876449f422ae6f8a
    resource: repo://supabase/migrations/20251202172650_add_rls_policies_for_org_isolation.sql
  - id: openwiki-source-f3707b5a5f2f3aaa7c3e2ac7
    resource: repo://supabase/migrations/20251220000000_create_claude_conversations_table.sql
  - id: openwiki-source-2816b2f543bdc890a591add3
    resource: repo://supabase/migrations/20251223160000_create_instance_identity_table.sql
  - id: openwiki-source-4e7dafba30392746aa5ba9fe
    resource: repo://supabase/migrations/20260111000000_enable_realtime_notifications.sql
generated: { by: "claude-code", at: "2026-09-17T11:09:25.960Z" }
---

# Data Model and Migrations

Postgres is more than InRes's datastore - it is the integration bus between
services (see [architecture overview](../architecture/overview.md)). That makes
the schema a cross-language contract shared by Go, Python and TypeScript, and
makes migration discipline load-bearing.

Related: [Tenancy and authorization](../concepts/tenancy-and-authorization.md) ·
[Escalation and notifications](../workflows/escalation-and-notifications.md) ·
[Deployment](../operations/deployment.md)

---

## Migration convention

Migrations live in `supabase/migrations/` and are named
`{YYYYMMDDHHMMSS}_{description}.sql`. **Ordering is lexicographic on that
timestamp prefix** - `findMigrationFiles` walks the directory collecting `.sql`
files and sorts the paths as strings, which works precisely because the prefix
is zero-padded and fixed-width.

The sequence begins with `20250925091841_remote_schema.sql`, which creates the
`pgmq` schema and installs the PGMQ extension, followed by seed data and queue
creation. Everything after is incremental.

There is no down-migration mechanism. Reversing a change means writing a new
forward migration.

Because migrations are applied in one global order across all services, adding a
column the Go API and the Python agent both read requires the migration to land
before either deploys.

---

## How migrations are applied

Three paths exist, for three environments.

**Local development** - the Supabase CLI: `supabase link` then
`supabase db push`.

**The `inres` CLI** - `deploy/inres-cli` provides `inres migrate`, which
auto-detects the approach. It requires `DATABASE_URL`, and `SUPABASE_URL` unless
`--direct` is passed; it extracts the project ref from the Supabase URL with a
regex against `https://{ref}.supabase.co`, tries the Supabase CLI first, and
falls back to direct `psql`. `--path` overrides the migrations directory and
`--dry-run` lists what would be applied without applying it.

**Kubernetes** - a Helm `Job` annotated as a `pre-install,pre-upgrade` hook with
`hook-weight: -5`, so it runs **before** any application pod starts and before
other hooks. `restartPolicy: Never` with a `backoffLimit` (default 3) gives
bounded retries, and `hook-delete-policy: before-hook-creation,hook-succeeded`
cleans up old jobs. A failed migration therefore blocks the release rather than
letting pods start against an unmigrated schema.

A fourth, narrower path exists: `server/api/cmd/migrate` is a small binary that
applies one hardcoded file (`migrations/create_monitors_tables.sql`). It is not
the general mechanism.

---

## PGMQ queues

PGMQ is installed as a Postgres extension in its own `pgmq` schema. Queues are
created declaratively in migrations with `SELECT pgmq.create(...)`, and
`IncidentAnalyticsService.CreateQueueIfNotExists` creates one at startup -
tolerating an error because PGMQ's create is idempotent.

| Queue | Producer | Consumer |
|---|---|---|
| `incident_notifications` | API (`LightweightNotificationSender`) and notification worker | Python Slack worker |
| `general_notifications` | Notification worker | Currently disabled |
| `incident_actions` | Slack worker (`slack_repository.py`) | Go notification worker |
| `slack_feedback` | Go notification worker (`sendSlackFeedbackMessage`) | Python Slack worker |
| `incident_analysis_queue` | Go `IncidentAnalyticsService` | Agent `services/analytics.py` |
| `marketplace_cleanup_queue` | Created in a migration; no code reference | - |

`incident_analysis_queue` is the one queue that crosses the language boundary by
name alone: the Go analytics service enqueues onto it and the Python agent's
analytics module drains it, with nothing but the string literal
`"incident_analysis_queue"` shared between them.

`marketplace_cleanup_queue` is created by a migration but is not referenced from
any service code in the tree.

### The two-directional incident flow

`incident_notifications` and `incident_actions` form a loop worth understanding:

- The API enqueues a notification onto **`incident_notifications`**; the Python
  Slack worker drains it and posts an interactive Slack message.
- A user clicks Acknowledge or Resolve in Slack; the Slack worker enqueues onto
  **`incident_actions`**; the Go notification worker drains that and applies the
  state change.
- Having applied it, the Go worker enqueues onto **`slack_feedback`**; the Slack
  worker drains that and updates the message already posted, so the button the
  user pressed reflects the outcome.

The migration creating `incident_actions` explains the reasoning directly: it
handles actions triggered from external sources such as Slack and webhooks and
**routes them through the proper API layer for consistent business logic** -
rather than letting the Slack worker mutate incidents directly.

### Consumption pattern

Consumers use `pgmq.read(queue, vt, batch_size)` with a visibility timeout of 30
seconds and explicitly `pgmq.delete(queue, msg_id)` after successful processing.
Because a message becomes visible again if it is not deleted, a crashed consumer
does not lose work - but handlers must be idempotent, since redelivery is
possible. `pgmq.send` accepts an optional delay, which is what schedules
escalation steps into the future.

`pgmq.metrics(queue)` backs the queue statistics the API exposes.

Note that in the Go notification worker, the `incident_notifications` and
`general_notifications` processing calls are commented out - the worker actively
processes only `incident_actions`, with Slack delivery delegated to the Python
consumer.

---

## Row-level security

`20251202172650_add_rls_policies_for_org_isolation.sql` adds defence in depth
beneath the application's ReBAC layer. Its structure is three helper functions
plus per-table policies.

The helpers are all `SECURITY DEFINER` and `STABLE`:

- `get_user_organizations()` - org ids where `auth.uid()` has a membership.
- `get_user_projects()` - project ids likewise.
- `user_has_org_access(org_id)` - an `EXISTS` check.

`STABLE` matters for performance: it lets the planner evaluate the function once
per statement rather than per row. `SECURITY DEFINER` lets the function read
`memberships` even where the calling role cannot.

All three read the **same `memberships` table** the Go authorizer queries, so
the two enforcement layers cannot disagree about who belongs to what. Policies
then restrict each table - for example, `organizations` is readable only where
`id IN (SELECT get_user_organizations())`.

The migration header notes an ordering dependency: it must run **after**
`20251202_add_organization_tenant_isolation.sql`, which adds the columns these
policies reference. The timestamp convention is what guarantees that.

RLS applies to connections authenticated as a Supabase user, which is how the
frontend's direct Supabase access (Realtime, Storage) stays tenant-isolated. The
Go API connects with elevated credentials and enforces isolation in the
application layer instead - the two mechanisms cover different access paths, and
neither alone is sufficient.

---

## Schema domains

`server/api/db/model.go` (~1150 lines) carries the bulk of the Go-side model
definitions, with `incident_models.go` separated out and `system_users.go` for
system accounts. `db.go` itself is a thin constructor pair for the Postgres and
Redis clients - there is no ORM, and services write SQL directly with
parameterised queries.

The migration history traces the product's growth in identifiable phases:

- **Scheduling** (Oct 2025) - shift alterations, composite indexes for scheduler
  performance, rotation metadata, and the `effective_shifts` view that
  materialises override resolution (see
  [on-call scheduling](../workflows/oncall-scheduling.md)).
- **Agent extensibility** (Nov 2025) - storage RLS, skills storage,
  marketplaces, installed plugins, `user_mcp_servers`, `claude_memory`,
  `user_allowed_tools`, memory scopes.
- **Monitoring** (Nov 2025) - monitor integrations and features, DNS and
  certificate monitoring, configurable webhook URLs, KV namespace and worker URL
  on deployments.
- **Mobile and zero trust** (Nov-Dec 2025) - `mobile_sessions`,
  `agent_device_certs`, `instance_identity` (see
  [authentication and identity](../concepts/authentication-and-identity.md)).
- **Multi-tenancy** (Dec 2025) - org/project memberships, tenant isolation
  columns, RLS policies, migration of `group_members` into `memberships`,
  `project_id` on integrations, API keys.
- **Conversations** (Dec 2025) - `agent_sessions`, `claude_conversations`,
  `claude_messages`, `conversation_shares`, `agent_audit_logs`.
- **Recent** (2026) - marketplace updates, an AI-pilot API key seed, org
  scoping on monitors, uptime providers, realtime notifications, additional
  integration types.

Two migrations are notable for how they change behaviour rather than shape:
`20251127150000_add_fulltext_search_to_incidents.sql` is what makes the agent's
`search_incidents` tool possible, and
`20260111000000_enable_realtime_notifications.sql` enables the Supabase Realtime
publication the frontend subscribes to (see
[web application](../frontend/web-application.md)).
