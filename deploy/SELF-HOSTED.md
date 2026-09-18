# Self-hosted install runbook

Bringing up the platform layer — Postgres and Supabase — on an existing
Kubernetes cluster, before any inres component is installed.

Everything here runs **once per environment**. The inres chart is the last
step, and it assumes all of this already exists.

## Why this order

inres does not own its identity or its data. The Go API and the agent both
verify Supabase-issued JWTs on every request, and both talk to Postgres
directly. Installing the application first gives you pods that crash-loop
against a database that is not there, with errors that point at the wrong
thing.

The order that matters most, and the one that is least obvious:

```
Postgres  ->  bootstrap roles  ->  Supabase services  ->  inres migrations
 (4)              (5)                    (6)                    (7)
```

The inres migrations come **after** the Supabase services, not before. They
reference `auth.users` and `storage.buckets`, and those schemas are created by
GoTrue and supabase-storage when they run their own migrations at startup. Run
the migrations first and 22 of the 49 files fail.

Each step below ends with a check. Do not move on until it passes — a failure
caught at step 4 costs minutes, the same failure discovered at step 10 looks
like an application bug.

## What you need first

| | |
|---|---|
| Kubernetes | 1.28+, with `kubectl` pointed at it |
| Storage | a default StorageClass that can provision `ReadWriteOnce` volumes. On EKS this means the **EBS CSI driver add-on** — it is not built in |
| Ingress | an ingress controller, or a load balancer you can point at a Service |
| Object storage | an S3 bucket (or compatible) for Postgres backups and Supabase Storage |
| CLI tools | `helm` 3.12+, `kubectl`, `openssl`, `docker` |

---

## Step 1 — Namespace and secrets

**Purpose:** every later step reads these. Creating them first means no step
half-succeeds and leaves you reconciling state.

Supabase's `anon` and `service_role` keys are not random strings. They are
JWTs signed with your JWT secret, carrying a `role` claim. Generating them
from anything other than the secret you configure produces tokens that look
valid and are rejected at runtime.

```bash
kubectl create namespace supabase

# One secret, used by Postgres, GoTrue, and every inres service.
export JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n=' | head -c 64)"
export PG_PASSWORD="$(openssl rand -base64 32 | tr -d '\n=/+')"
```

Mint the two API keys from that secret. Any JWT tool will do; this uses
`python3` so there is nothing to install:

```bash
python3 - <<'PY'
import base64, hashlib, hmac, json, os, time

def sign(role, secret, years=5):
    h = {"alg": "HS256", "typ": "JWT"}
    p = {"role": role, "iss": "supabase",
         "iat": int(time.time()), "exp": int(time.time()) + years*365*24*3600}
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    msg = b(h) + b"." + b(p)
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), msg, hashlib.sha256).digest()).rstrip(b"=")
    return (msg + b"." + sig).decode()

s = os.environ["JWT_SECRET"]
print("ANON_KEY=" + sign("anon", s))
print("SERVICE_ROLE_KEY=" + sign("service_role", s))
PY
```

Export both, then store everything:

```bash
kubectl -n supabase create secret generic supabase-jwt \
  --from-literal=secret="$JWT_SECRET" \
  --from-literal=anonKey="$ANON_KEY" \
  --from-literal=serviceKey="$SERVICE_ROLE_KEY"

kubectl -n supabase create secret generic supabase-db \
  --from-literal=username=postgres \
  --from-literal=password="$PG_PASSWORD"
```

**Check:** `kubectl -n supabase get secret supabase-jwt supabase-db` lists both.

Keep `$JWT_SECRET` and both keys somewhere durable now. They are needed again
in step 9, and the keys cannot be regenerated without reissuing every token.

---

## Step 2 — CloudNativePG operator

**Purpose:** CNPG turns Postgres from a pod you babysit into a managed
resource — failover, replicas and point-in-time recovery come from the
operator rather than from runbooks.

```bash
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.0.yaml
```

**Check:**

```bash
kubectl -n cnpg-system wait --for=condition=Available deploy/cnpg-controller-manager --timeout=180s
```

---

## Step 3 — Build the Postgres image

**Purpose:** neither stock image works here. `supabase/postgres` is Nix-based
and does not match CNPG's layout, uid or barman expectations; CNPG's own image
has no `pgmq`, which the Go API and the Slack worker call directly. Without
`pgmq` the escalation and notification queues do not exist.

See [`deploy/postgres/README.md`](postgres/README.md) for the detail.

A published image already exists and can be used as-is:

```
ghcr.io/phonginreallife/inres-postgres:17-pgmq
```

It is built for `linux/amd64` only. If your nodes are Graviton, rebuild:

```bash
docker buildx build --platform linux/arm64 \
  -t ghcr.io/<owner>/inres-postgres:17-pgmq --push deploy/postgres
```

**Check:** the tag resolves for the architecture your nodes run.

```bash
docker manifest inspect ghcr.io/phonginreallife/inres-postgres:17-pgmq \
  | grep -A1 '"architecture"'
```

If the package is private, the cluster needs a pull secret:

```bash
kubectl -n supabase create secret docker-registry ghcr \
  --docker-server=ghcr.io --docker-username=<user> --docker-password=<token>
```

and `imagePullSecrets: [{ name: ghcr }]` on the Cluster spec in step 4.

### What the image does and does not carry

Verified against the built image:

| | |
|---|---|
| Present | `pgmq`, `vector`, `pgcrypto`, `uuid-ossp`, `pg_stat_statements`, `pgaudit`, and the rest of contrib |
| Absent | `pg_net`, `pg_graphql`, `supabase_vault` |

The three absent ones are Supabase-specific builds that this codebase never
calls. Their `CREATE EXTENSION` lines have been removed from
`20250925091841_remote_schema.sql`, so there is nothing to do here - but if you
restore that file from a hosted dump, step 7 will fail on them again.

---

## Step 4 — Postgres cluster

**Purpose:** the database everything else depends on. Two settings here are
load-bearing:

- `wal_level: logical` and replication slots — Supabase Realtime consumes a
  logical replication slot. Without these it starts, connects, and silently
  never delivers an event.
- `instances: 3` — one primary, two replicas, automatic failover.

Backups are deliberately **not** configured here. They are step 8, once the
cluster is real and an IAM role exists. Configuring a backup destination the
cluster cannot actually reach is worse than configuring none: `archive_command`
fails on every WAL segment, Postgres refuses to recycle WAL it has not
archived, `walStorage` fills, and the primary stops accepting writes. On an
empty cluster there is nothing to lose by waiting.

### Preflight

All four must be true, or the apply fails in a way that looks like a CNPG
problem and is not:

```bash
kubectl get crd clusters.postgresql.cnpg.io                 # step 2 completed
kubectl get namespace supabase                              # step 1 completed
kubectl -n supabase get secret supabase-db                  # step 1 completed
kubectl get storageclass                                    # one marked (default)
```

The last one catches the most common EKS failure. Without a default
StorageClass backed by the EBS CSI driver add-on, the PVCs sit `Pending` and
the cluster never reaches `Ready` - with no error that mentions storage.

```yaml
# postgres-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: supabase-db
  namespace: supabase
spec:
  instances: 3
  imageName: ghcr.io/phonginreallife/inres-postgres:17-pgmq

  bootstrap:
    initdb:
      database: postgres
      owner: postgres
      secret:
        name: supabase-db

  postgresql:
    parameters:
      wal_level: logical           # required by Realtime
      max_replication_slots: "10"
      max_wal_senders: "10"

  storage:
    size: 50Gi                     # data; grows with incident history
  walStorage:
    size: 20Gi                     # separate volume: a WAL spike must not
                                   # fill the data volume and stop writes

  # No `backup:` block yet - see step 8.

  resources:
    requests: { cpu: "1", memory: 2Gi }
    limits:   { cpu: "2", memory: 4Gi }
```

```bash
kubectl apply -f postgres-cluster.yaml
kubectl -n supabase wait --for=condition=Ready cluster/supabase-db --timeout=600s
```

**Check:** three pods `Running`, and `pgmq` is present:

```bash
kubectl -n supabase exec -it supabase-db-1 -- \
  psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pgmq; SELECT extversion FROM pg_extension WHERE extname='pgmq';"
```

An empty result means the image is wrong. Fix it here — every later failure
would be a confusing symptom of this one.

---

## Step 5 — Bootstrap roles and schemas

**Purpose:** the inres migrations do not stand alone. They were produced by
`supabase db pull` against a hosted project, so they assume everything
Supabase's own base image creates at init time. A CNPG cluster has none of it.

This is the step that is easy to skip and expensive to skip. Measured against
`supabase/migrations/`:

| Assumed to exist | Referenced by | Created by the migrations? | Created here? |
|---|---|---|---|
| roles `anon`, `authenticated`, `service_role` | ~110 `GRANT` and RLS `TO` clauses | no | yes |
| role `authenticator` | PostgREST/GoTrue role switching | no | yes |
| schema `extensions` | every `CREATE EXTENSION ... WITH SCHEMA "extensions"` | no | yes |
| publication `supabase_realtime` | 11 `ALTER PUBLICATION` calls | no (guarded, so silently skipped if absent) | yes |
| schema `auth`, `auth.uid()`, `auth.users` | 100 `auth.uid()` calls, 7 foreign keys | no | **no - step 6** |
| schema `storage`, `storage.buckets/objects` | 28 references | no | **no - step 6** |

The last two rows are why migrations are step 7 and not this step. `auth` and
`storage` are created by GoTrue and supabase-storage when they run their own
migrations at startup, so the services have to come up first. Run the inres
migrations before that and 22 of the 49 files fail.

```bash
kubectl -n supabase port-forward svc/supabase-db-rw 5432:5432 &
export DATABASE_URL="postgresql://postgres:$PG_PASSWORD@localhost:5432/postgres"
```

```sql
-- bootstrap.sql - run once, before any migration
CREATE SCHEMA IF NOT EXISTS extensions;

-- PostgREST/GoTrue role model. anon and authenticated are what RLS policies
-- are written against; service_role bypasses RLS and is what the Go API and
-- the agent use. None of them may log in directly.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticator') THEN
    EXECUTE format(
      'CREATE ROLE authenticator LOGIN NOINHERIT PASSWORD %L',
      current_setting('bootstrap.authenticator_password'));
  END IF;
END
$$;

GRANT anon, authenticated, service_role TO authenticator;
GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;

-- Realtime publishes through this. The migrations add tables to it only if it
-- already exists, so creating it empty here is what makes those calls take
-- effect rather than silently skip.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
END
$$;
```

The `authenticator` password is passed as a session setting rather than
interpolated into the file, because psql does not substitute `-v` variables
inside dollar-quoted blocks. `-c` and `-f` share one session, so the `SET`
reaches the `DO` block:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "SET bootstrap.authenticator_password = '$PG_PASSWORD';" -f bootstrap.sql
```

Re-running is safe; every statement is guarded.

**Check:**

```bash
psql "$DATABASE_URL" -At -c \
  "SELECT rolname, rolcanlogin, rolbypassrls FROM pg_roles
     WHERE rolname IN ('anon','authenticated','service_role','authenticator') ORDER BY 1;"
```

Expected, exactly:

```
anon|f|f
authenticated|f|f
authenticator|t|f
service_role|f|t
```

`service_role` must show `rolbypassrls = t` - it is the role the Go API and the
agent connect as, and RLS policies are not written to admit it.

---
## Step 6 — Supabase services

**Purpose:** Auth, Storage, Realtime and the gateway that fronts them. The
community chart is used here rather than hand-written manifests — four
Deployments with interlocking config is a maintenance burden with no upside.

Only four components are enabled. This codebase makes **no** PostgREST calls
(no `.from('table')` anywhere — all data access goes through the Go API), and
uses no Edge Functions, so those are switched off along with the developer
tooling.

```yaml
# supabase-values.yaml
secret:
  jwt:
    existingSecret: supabase-jwt
  db:
    existingSecret: supabase-db

db:
  enabled: false                  # CNPG owns Postgres; do not run the bundled one

auth:
  enabled: true
  environment:
    GOTRUE_DB_DATABASE_URL: "postgres://postgres:$(DB_PASSWORD)@supabase-db-rw:5432/postgres"
    GOTRUE_SITE_URL: "https://inres.example.com"
    GOTRUE_JWT_EXP: "3600"

storage:
  enabled: true
  environment:
    STORAGE_BACKEND: s3           # not 'file': the agent creates a bucket per
    GLOBAL_S3_BUCKET: <bucket>    # user, and a PVC makes that your backup problem
    REGION: <region>

realtime:
  enabled: true

rest:      { enabled: false }     # unused - no .from() calls anywhere
functions: { enabled: false }
studio:    { enabled: false }     # developer tooling
meta:      { enabled: false }
analytics: { enabled: false }
imgproxy:  { enabled: false }
vector:    { enabled: false }

kong:
  enabled: true
```

```bash
helm repo add supabase https://supabase-community.github.io/supabase-kubernetes
helm upgrade --install supabase supabase/supabase \
  -n supabase -f supabase-values.yaml
```

**Check:** auth answers, and a token round-trips:

```bash
kubectl -n supabase port-forward svc/supabase-kong 8000:8000 &

curl -s http://localhost:8000/auth/v1/health -H "apikey: $ANON_KEY"

curl -s http://localhost:8000/auth/v1/admin/users \
  -H "apikey: $SERVICE_ROLE_KEY" -H "Authorization: Bearer $SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@example.com","password":"'"$(openssl rand -hex 12)"'","email_confirm":true}'
```

A `401` here almost always means the keys were minted from a different secret
than the one GoTrue is using. Go back to step 1 rather than debugging forward.

**Second check, and step 7 depends on it:** both services have run their own
migrations against the database.

```bash
psql "$DATABASE_URL" -At -c \
  "SELECT nspname FROM pg_namespace WHERE nspname IN ('auth','storage') ORDER BY 1;"
```

Both must be listed. A healthy pod is not the same as a migrated schema - Auth
reports ready before it has finished, so check the database rather than the
Deployment.

---

## Step 7 — Apply inres migrations

**Purpose:** creates the inres schema, the RLS policies and the PGMQ queues.

This runs after step 6 because the migrations depend on `auth` and `storage`,
which GoTrue and supabase-storage create at startup. Confirm they are there
before starting - this check is the whole reason for the ordering:

```bash
psql "$DATABASE_URL" -At -c \
  "SELECT nspname FROM pg_namespace WHERE nspname IN ('auth','storage') ORDER BY 1;"
psql "$DATABASE_URL" -At -c "SELECT to_regproc('auth.uid') IS NOT NULL;"
```

You need `auth`, `storage`, and `t`. If `auth.uid` is missing, GoTrue has not
finished its own migrations yet - wait, do not proceed.

```bash
for f in supabase/migrations/*.sql; do
  echo "applying $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

Keep `ON_ERROR_STOP=1`. With steps 5 and 6 done, all 49 files apply cleanly;
an error here is a real finding, not noise to skip past.

**Check:**

```bash
psql "$DATABASE_URL" -c "\dt public.*" | head
psql "$DATABASE_URL" -c "SELECT queue_name FROM pgmq.list_queues();"
```

You should see the inres tables and the queues from
`20250926160648_queue_initial_data.sql`.

---

## Step 8 — Enable backups

**Purpose:** now that the cluster is real, give it somewhere to archive to.
Deferred from step 4 because a backup destination the cluster cannot reach will
wedge the primary; see that step for why.

CNPG creates the cluster's ServiceAccount itself and reconciles it, so
`kubectl annotate` does not stick. `serviceAccountTemplate` is the only hook.

First the IAM role. Trust policy, bound to your cluster's OIDC provider and to
that one ServiceAccount:

```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<account-id>:oidc-provider/<oidc-issuer>" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "<oidc-issuer>:sub": "system:serviceaccount:supabase:supabase-db",
      "<oidc-issuer>:aud": "sts.amazonaws.com"
    }
  }
}
```

Permissions, scoped to the one prefix rather than the bucket:

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject",
                 "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": "arn:aws:s3:::<your-bucket>/supabase-db/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<your-bucket>",
      "Condition": { "StringLike": { "s3:prefix": "supabase-db/*" } }
    }
  ]
}
```

Then add both blocks to the Cluster and re-apply:

```yaml
spec:
  # CNPG owns this ServiceAccount. This template is the only way to annotate it.
  serviceAccountTemplate:
    metadata:
      annotations:
        eks.amazonaws.com/role-arn: arn:aws:iam::<account-id>:role/inres-supabase-db-backup

  backup:
    barmanObjectStore:
      destinationPath: s3://<your-bucket>/supabase-db
      s3Credentials:
        inheritFromIAMRole: true
      wal:
        compression: gzip
    retentionPolicy: "30d"
```

`inheritFromIAMRole: true` does not mean "use the node role". It means "use no
Secret, walk the AWS SDK credential chain": env vars, then the IRSA web
identity token, then IMDS. The annotation above is what puts a credential at
the second step. Without it the chain falls through to IMDS, which EKS managed
node groups block by default via a hop limit of 1 - and if it does succeed, it
means every pod on the node can write to your backup bucket.

Not on EKS, or no OIDC provider? Drop `inheritFromIAMRole` and point
`s3Credentials` at a Secret with `accessKeyId` and `secretAccessKey` keys
instead.

WAL archiving alone recovers nothing - there has to be a base backup to replay
onto. That is a separate resource:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: supabase-db-daily
  namespace: supabase
spec:
  schedule: "0 0 2 * * *"      # six fields: CNPG includes seconds
  backupOwnerReference: self
  cluster:
    name: supabase-db
```

**Check:** archiving is working, not merely configured.

```bash
kubectl -n supabase get cluster supabase-db \
  -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")]}'

kubectl create -f - <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata: { name: smoke-test, namespace: supabase }
spec:
  cluster: { name: supabase-db }
EOF

kubectl -n supabase get backup smoke-test -w   # want phase: completed
```

A `ContinuousArchiving` condition of `False` means the credential chain did not
resolve. Fix it now: while it stays false WAL accumulates on `walStorage` and
is never recycled.

One CNPG note: recent releases moved object-store backup to the Barman Cloud
plugin and deprecated the in-tree `barmanObjectStore` field. Check the release
notes for the operator version you pinned in step 2 before assuming the shape
above is current.

---
## Step 9 — Point inres at it

**Purpose:** the application reads all of this from one Secret. Both the Go API
and the agent verify HS256 tokens against `supabase_jwt_secret`; if it does not
match what GoTrue signs with, every request is rejected as unauthenticated and
the chat WebSocket closes with `4001`.

```yaml
# config.yaml — becomes the inres-secrets Secret
database_url: "postgresql://postgres:<PG_PASSWORD>@supabase-db-rw.supabase.svc.cluster.local:5432/postgres?sslmode=disable"

supabase_url: "http://supabase-kong.supabase.svc.cluster.local:8000"
public_supabase_url: "https://inres.example.com"
supabase_anon_key: "<ANON_KEY>"
supabase_service_role_key: "<SERVICE_ROLE_KEY>"
supabase_jwt_secret: "<JWT_SECRET>"

# Exactly one Anthropic credential. An API key takes precedence wherever it is
# found, so leave it empty to use an OAuth token.
anthropic_api_key: "sk-ant-..."

ai_agent:
  model: "claude-opus-5"
  require_tool_approval: true
  max_concurrent_cli: 8
```

```bash
kubectl create namespace inres
kubectl -n inres create secret generic inres-secrets --from-file=config.yaml
```

**Check:** `kubectl -n inres get secret inres-secrets -o jsonpath='{.data.config\.yaml}' | base64 -d | head`
shows your values and no placeholders.

---

## Step 10 — Install inres

Only now.

```bash
helm upgrade --install inres oci://ghcr.io/phonginreallife/charts/inres \
  -n inres --version <chart-version>
```

**Check:**

```bash
kubectl -n inres get pods            # all Running and Ready
kubectl -n inres logs deploy/inres-ai | grep "Chat Agent:"
```

The agent logs its resolved model and settings at startup. Then open the UI,
sign in with the user from step 6, and ask the assistant a question — that one
request crosses the frontend, Kong, the agent, the Claude CLI, the incident
tools, the Go API and Postgres, which is the fastest way to confirm the whole
chain.

---

## Notes on what this leaves out

**Realtime may not be worth operating.** It is the fiddliest component — its
own role, a logical replication slot, its own failure modes — and the Go API
already broadcasts over plain HTTP to `/realtime/v1/api/broadcast`. A small SSE
endpoint on the API would replace it and remove a service. Worth deciding
before you commit to running it.

**The agent is single-replica by design.** Its volumes are `ReadWriteOnce` and
conversation resume reads the CLI transcript on the pod that created the
session. The chart refuses `components.ai.autoscaling` for that reason. Scale
`api` and `web` freely.

**Backups are only as good as their restore.** Before this carries anything you
care about, run a recovery into a throwaway cluster and confirm the data is
there. `barmanObjectStore` reports success long before anyone has proved a
restore works.

**What here is verified, and what is not.** Worth knowing which is which before
you trust a step.

| | |
|---|---|
| Verified | The Postgres image: it builds, is published, `CREATE EXTENSION pgmq` succeeds, and a queue round-trips a message. The extension inventory in step 3 was read off the built image. |
| Verified | The step 5 bootstrap SQL: run against that image, it succeeds, is idempotent on a second run, and produces exactly the four roles with the attributes the check expects. |
| Verified | The step 5 -> 6 -> 7 ordering. Against a database with only the bootstrap applied, 22 of the 49 migration files fail - 13 on missing `auth`, 10 on missing `storage`, the rest cascading from those. With `auth` and `storage` present, all 49 apply cleanly. |
| Not run | The CNPG Cluster spec, the backup and IRSA config, and the Supabase chart values. These are written from component requirements and have not been applied to a live cluster. |
| Not run | Step 7 was verified against stand-ins for `auth` and `storage`, not against GoTrue and supabase-storage themselves. The ordering is proven; the exact shape those services create is not. |

Treat step 4, step 6 and step 8 as the places to slow down.
