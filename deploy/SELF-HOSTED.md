# Self-hosted install runbook

Bringing up the platform layer - Postgres and Supabase - on an existing
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

Each step below ends with a check. Do not move on until it passes - a failure
caught at step 4 costs minutes, the same failure discovered at step 10 looks
like an application bug.

## What you need first

| | |
|---|---|
| Kubernetes | 1.28+, with `kubectl` pointed at it |
| Storage | a default StorageClass that can provision `ReadWriteOnce` volumes. On EKS this means the **EBS CSI driver add-on** - it is not built in |
| Ingress | an ingress controller, or a load balancer you can point at a Service |
| Object storage | an S3 bucket (or compatible) for Postgres backups and Supabase Storage |
| CLI tools | `helm` 3.12+, `kubectl`, `openssl`, `docker`, and **`psql`** - steps 5 and 7 are unusable without a Postgres client. On macOS, `brew install libpq` gives you one without a server; it is keg-only, so add `$(brew --prefix libpq)/bin` to your PATH |

---

## Step 1 - Namespace and secrets

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
`python3` so there is nothing to install. Note the `read` - the keys have to
land in shell variables, not just on your screen:

```bash
read -r ANON_KEY SERVICE_ROLE_KEY <<< "$(python3 - <<'PY'
import base64, hashlib, hmac, json, os, time

def sign(role, secret, years=5):
    now = int(time.time())
    h = {"alg": "HS256", "typ": "JWT"}
    p = {"role": role, "iss": "supabase", "iat": now, "exp": now + years*365*24*3600}
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    msg = b(h) + b"." + b(p)
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), msg, hashlib.sha256).digest()).rstrip(b"=")
    return (msg + b"." + sig).decode()

s = os.environ["JWT_SECRET"]
print(sign("anon", s), sign("service_role", s))
PY
)"
export ANON_KEY SERVICE_ROLE_KEY
```

An earlier version of this runbook printed the keys and told you to "export
both" without showing how. Miss that and `$ANON_KEY` is empty, the secret is
created with empty values, and nothing complains - Kong serves requests happily
until the first authenticated call, several steps later, returns `401`.

Now store everything:

```bash
kubectl -n supabase create secret generic supabase-jwt \
  --from-literal=secret="$JWT_SECRET" \
  --from-literal=anonKey="$ANON_KEY" \
  --from-literal=serviceKey="$SERVICE_ROLE_KEY"

# Type matters. CNPG only acts on a superuserSecret of type
# kubernetes.io/basic-auth; given an Opaque secret it holds the reference and
# silently never sets a password. See step 4.
kubectl -n supabase create secret generic supabase-db \
  --type=kubernetes.io/basic-auth \
  --from-literal=username=postgres \
  --from-literal=password="$PG_PASSWORD"
```

**Check:** all three values are non-empty. Length, not existence - an empty
value still produces a key.

```bash
for k in secret anonKey serviceKey; do
  printf '%-12s %s\n' "$k" \
    "$(kubectl -n supabase get secret supabase-jwt -o jsonpath="{.data.$k}" | base64 -d | wc -c)"
done
```

`secret` should be 64, and both keys a few hundred. Any `0` means the `read`
above did not run.

Keep `$JWT_SECRET` and both keys somewhere durable now. They are needed again
in step 9, and the keys cannot be regenerated without reissuing every token.

---

## Step 2 - CloudNativePG operator

**Purpose:** CNPG turns Postgres from a pod you babysit into a managed
resource - failover, replicas and point-in-time recovery come from the
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

## Step 3 - Build the Postgres image

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

## Step 4 - Postgres cluster

**Purpose:** the database everything else depends on. Two settings here are
load-bearing:

- `wal_level: logical` and replication slots - Supabase Realtime consumes a
  logical replication slot. Without these it starts, connects, and silently
  never delivers an event.
- `instances: 3` - one primary, two replicas, automatic failover.

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

  # Both lines are required, and neither is the default.
  #
  # Since 1.21 CNPG defaults enableSuperuserAccess to false, and that is not a
  # passive default - the operator actively keeps the postgres role's password
  # NULL. bootstrap.initdb.secret above sets the *application* user, which is a
  # different thing, so without this every password connection as postgres is
  # rejected no matter what the secret holds.
  #
  # superuserSecret points CNPG at the secret from step 1 rather than having it
  # generate its own; omit it and the password lives in a generated
  # <cluster>-superuser secret you then have to read back. The secret must be
  # of type kubernetes.io/basic-auth or CNPG ignores it without logging
  # anything.
  #
  # Step 6 also needs this: the Supabase services connect over the network with
  # a password.
  enableSuperuserAccess: true
  superuserSecret:
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

Size `instances` and `resources` to the nodes you actually have. Requests are
what the scheduler reserves, so a request larger than a single node's
allocatable capacity is unschedulable forever - and because the cluster
autoscaler simulates against the same instance type, adding nodes does not
help. Check before applying:

```bash
kubectl get nodes -o custom-columns=\
'NODE:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory'
```

For a first run on small nodes, `instances: 1` with `250m`/`512Mi` requests and
10Gi/5Gi volumes is enough to validate every later step. Replicas prove
failover; they prove nothing about bootstrap, service startup or migrations.

```bash
kubectl apply -f postgres-cluster.yaml
```

Poll rather than using `kubectl wait` if your API server drops long watches -
a `client connection lost` error there says nothing about the cluster:

```bash
while :; do kubectl -n supabase get clusters.postgresql.cnpg.io supabase-db --no-headers; sleep 10; done
```

Use the fully-qualified `clusters.postgresql.cnpg.io`. On clusters running
Rancher, the short name `cluster` resolves to `clusters.management.cattle.io`
instead and returns a bewildering `NotFound`.

**Check:** pods `Running`, and `pgmq` is present:

```bash
kubectl -n supabase exec -it supabase-db-1 -- \
  psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pgmq; SELECT extversion FROM pg_extension WHERE extname='pgmq';"
```

An empty result means the image is wrong. Fix it here - every later failure
would be a confusing symptom of this one.

---

## Step 5 - Bootstrap roles and schemas

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
| roles `supabase_auth_admin`, `supabase_storage_admin`, `supabase_admin` | the Supabase services authenticate as these, not as `postgres` | no | yes |
| schema `auth`, `auth.uid()`, `auth.users` | 100 `auth.uid()` calls, 7 foreign keys | no | **contents come from step 6** |
| schema `storage`, `storage.buckets/objects` | 28 references | no | **contents come from step 6** |

The last two rows are why migrations are step 7 and not this step. The schemas
are created here so the services can own them, but their *tables* are written
by GoTrue and supabase-storage when they run their own migrations at startup.
Run the inres migrations before that and 22 of the 49 files fail: 13 on a
missing `auth`, 10 on a missing `storage`, the rest cascading from those.

The service-role row is the one most likely to catch you. Each Supabase
component connects as its own role with its own schema, and none of them
connect as `postgres`:

| Service | Connects as | Owns |
|---|---|---|
| auth (GoTrue) | `supabase_auth_admin` | `auth` |
| storage | `supabase_storage_admin` | `storage` |
| realtime | `supabase_admin` | `_realtime` |

Write the SQL below to a **file**. Pasting it into a shell does not work: `$$`
expands to the shell's PID, and you get a screen of `command not found`. The
quoted heredoc delimiter is what prevents that.

```bash
kubectl -n supabase port-forward svc/supabase-db-rw 5432:5432 &
export DATABASE_URL="postgresql://postgres:$PG_PASSWORD@localhost:5432/postgres"
```

```bash
cat > bootstrap.sql <<'SQL'
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

-- Service roles. Each Supabase component logs in as its own role; they all
-- read the same password from the connection secret created in step 6.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'supabase_auth_admin') THEN
    EXECUTE format('CREATE ROLE supabase_auth_admin LOGIN NOINHERIT CREATEROLE PASSWORD %L',
                   current_setting('bootstrap.service_password'));
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'supabase_storage_admin') THEN
    EXECUTE format('CREATE ROLE supabase_storage_admin LOGIN NOINHERIT CREATEROLE PASSWORD %L',
                   current_setting('bootstrap.service_password'));
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'supabase_admin') THEN
    EXECUTE format('CREATE ROLE supabase_admin LOGIN NOINHERIT CREATEROLE CREATEDB REPLICATION BYPASSRLS PASSWORD %L',
                   current_setting('bootstrap.service_password'));
  END IF;
END
$$;

-- Each service migrates into a schema it must own.
CREATE SCHEMA IF NOT EXISTS auth      AUTHORIZATION supabase_auth_admin;
CREATE SCHEMA IF NOT EXISTS storage   AUTHORIZATION supabase_storage_admin;
CREATE SCHEMA IF NOT EXISTS _realtime AUTHORIZATION supabase_admin;

-- Without these, GoTrue's migrator creates its schema_migrations table in the
-- default search_path - "$user", public - and PostgreSQL 15+ no longer grants
-- CREATE on public, so it dies with "permission denied for schema public".
-- Realtime does not need one: it sets its own via DB_AFTER_CONNECT_QUERY.
ALTER ROLE supabase_auth_admin    SET search_path TO auth;
ALTER ROLE supabase_storage_admin SET search_path TO storage;

GRANT USAGE ON SCHEMA public     TO supabase_auth_admin, supabase_storage_admin;
GRANT USAGE ON SCHEMA extensions TO supabase_auth_admin, supabase_storage_admin, supabase_admin;
GRANT anon, authenticated, service_role TO supabase_admin;
SQL
```

Passwords are passed as session settings rather than interpolated into the
file, because psql does not substitute `-v` variables inside dollar-quoted
blocks. `-c` and `-f` share one session, so the `SET` reaches the `DO` block:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "SET bootstrap.authenticator_password = '$PG_PASSWORD';" \
  -c "SET bootstrap.service_password = '$PG_PASSWORD';" \
  -f bootstrap.sql
```

If the port-forward keeps dropping, pipe through the pod instead - it needs no
password, because local socket connections are trusted. Note the `printf`: the
`SET` has to be in the same stream, since psql exits after `-c` without reading
stdin.

```bash
{ printf "SET bootstrap.authenticator_password = '%s';\n" "$PG_PASSWORD"
  printf "SET bootstrap.service_password = '%s';\n"       "$PG_PASSWORD"
  cat bootstrap.sql
} | kubectl -n supabase exec -i supabase-db-1 -- psql -U postgres -v ON_ERROR_STOP=1
```

Re-running is safe; every statement is guarded.

**Check:**

```bash
psql "$DATABASE_URL" -At -c \
  "SELECT rolname, rolcanlogin, rolbypassrls FROM pg_roles
     WHERE rolname IN ('anon','authenticated','service_role','authenticator',
                       'supabase_auth_admin','supabase_storage_admin','supabase_admin')
     ORDER BY 1;"
```

Expected, exactly:

```
anon|f|f
authenticated|f|f
authenticator|t|f
service_role|f|t
supabase_admin|t|t
supabase_auth_admin|t|f
supabase_storage_admin|t|f
```

`service_role` must show `rolbypassrls = t` - it is the role the Go API and the
agent connect as, and RLS policies are not written to admit it. The three
`supabase_*` roles must show `rolcanlogin = t`, or step 6 crash-loops on
`password authentication failed`.

---
## Step 6 - Supabase services

**Purpose:** Auth, Storage, Realtime and the gateway that fronts them. The
community chart is used here rather than hand-written manifests - four
Deployments with interlocking config is a maintenance burden with no upside.

Only four components are enabled. This codebase makes **no** PostgREST calls
(no `.from('table')` anywhere - all data access goes through the Go API), and
uses no Edge Functions, so those are switched off along with the developer
tooling.

The services need a connection secret of their own. The chart reads `host`,
`port`, `database` and `password` from it, and the step 1 secret has
`username`/`password` instead - so make a second one rather than editing the
one CNPG owns:

```bash
PG_PASSWORD="$(kubectl -n supabase get secret supabase-db -o jsonpath='{.data.password}' | base64 -d)"

kubectl -n supabase create secret generic supabase-db-conn \
  --from-literal=host=supabase-db-rw.supabase.svc.cluster.local \
  --from-literal=port=5432 \
  --from-literal=database=postgres \
  --from-literal=password="$PG_PASSWORD"
```

```yaml
# supabase-values.yaml  - chart 0.8.0
secret:
  jwt:
    secretRef: supabase-jwt       # NOT existingSecret - see below
  db:
    secretRef: supabase-db-conn
    # Required, and not optional metadata. The template tests
    # `hasKey .Values.secret.db.secretRefKey "host"` - that is, whether you
    # declared the mapping, not whether the secret contains the key. Omit this
    # block and rendering aborts with "secret.db.host must be set".
    secretRefKey:
      host: host
      port: port
      database: database
      password: password

# Every component toggle lives under `deployment`. A top-level `db: {enabled:
# false}` is silently ignored, and you get a second Postgres alongside CNPG.
deployment:
  db:        { enabled: false }   # CNPG owns Postgres
  auth:      { enabled: true }
  storage:   { enabled: true }
  realtime:  { enabled: true }
  kong:      { enabled: true }
  rest:      { enabled: false }   # unused - no .from() calls anywhere
  functions: { enabled: false }
  studio:    { enabled: false }   # developer tooling
  meta:      { enabled: false }
  analytics: { enabled: false }
  imgproxy:  { enabled: false }
  vector:    { enabled: false }
  minio:     { enabled: false }

# The chart defaults to ingressClassName "nginx" with nginx-specific
# annotations. On a cluster with a different controller the admission webhook
# rejects the whole release. Nothing external needs to reach Supabase - inres
# talks to Kong over cluster DNS - so leave it off and port-forward to verify.
ingress:
  enabled: false
```

**`secretRef`, not `existingSecret`.** Helm ignores unknown values without
complaint, so the wrong name leaves the chart on its built-in defaults - which
for `secret.jwt` are the demo keys published in Supabase's own documentation,
identical on every default install. Auth then appears to work while accepting
tokens anyone can mint.

```bash
helm repo add supabase https://supabase-community.github.io/supabase-kubernetes
helm upgrade --install supabase supabase/supabase \
  --version 0.8.0 -n supabase -f supabase-values.yaml
```

Pin the chart version. These field names have already moved once.

Before installing, confirm the toggles took effect - `--dry-run` will not tell
you, because ignored keys are not errors:

```bash
helm template supabase supabase/supabase --version 0.8.0 \
  -n supabase -f supabase-values.yaml | grep -E "^kind: Deployment" -A3 | grep "name:"
```

Four names only: auth, storage, realtime, kong. Anything else - especially a
`db` - means a toggle is in the wrong place.

**Check:** auth answers, and a token round-trips. Note the service name carries
the release name, so installing as `supabase` gives `supabase-supabase-kong`:

```bash
kubectl -n supabase port-forward svc/supabase-supabase-kong 8000:8000 &

curl -s http://localhost:8000/auth/v1/health -H "apikey: $ANON_KEY"

curl -s http://localhost:8000/auth/v1/admin/users \
  -H "apikey: $SERVICE_ROLE_KEY" -H "Authorization: Bearer $SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@example.com","password":"'"$(openssl rand -hex 12)"'","email_confirm":true}'
```

A `401` here almost always means the keys were minted from a different secret
than the one GoTrue is using. Go back to step 1 rather than debugging forward.

**Second check, and step 7 depends on it:** both services have run their own
migrations against the database. Step 5 created the schemas, so their existence
proves nothing - count the tables in them:

```bash
psql "$DATABASE_URL" -At -c \
  "SELECT table_schema, count(*) FROM information_schema.tables
    WHERE table_schema IN ('auth','storage') GROUP BY 1 ORDER BY 1;"
psql "$DATABASE_URL" -At -c "SELECT to_regclass('auth.users') IS NOT NULL;"
```

Expect roughly 20 tables in `auth` and 10 in `storage`, and `t` for
`auth.users` - 7 migration files hold foreign keys into it. A healthy pod is
not the same as a migrated schema - Auth
reports ready before it has finished, so check the database rather than the
Deployment.

---

## Step 7 - Apply inres migrations

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

## Step 8 - Enable backups

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

Permissions. Object writes are confined to the one prefix; the bucket-level
grant is not:

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
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::<your-bucket>"
    }
  ]
}
```

Do **not** add an `s3:prefix` condition to the `ListBucket` statement, however
tempting it looks. Barman calls `HeadBucket` before anything else, `HeadBucket`
sends no prefix, so the condition can never match and every archive fails with
`403 Forbidden` - after the credential chain has resolved correctly, which
makes it read like an IRSA problem. Give the bucket a dedicated purpose
instead; there is then nothing in it for the prefix to protect.

`s3:GetBucketLocation` is what barman uses to resolve the region.

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

Substitute `<your-bucket>` everywhere before applying. Left literal, barman
reports `InvalidBucketName ... when calling the CreateBucket operation`, which
reads as though the bucket name is malformed rather than unsubstituted. It also
tells you barman creates the bucket if it is missing - and the policy above
deliberately does not grant `s3:CreateBucket`, so the bucket must already
exist.

`inheritFromIAMRole: true` does not mean "use the node role". It means "use no
Secret, walk the AWS SDK credential chain": env vars, then the IRSA web
identity token, then IMDS. The annotation above is what puts a credential at
the second step. Without it the chain falls through to IMDS, which EKS managed
node groups block by default via a hop limit of 1 - and if it does succeed, it
means every pod on the node can write to your backup bucket.

Not on EKS, or no OIDC provider? Drop `inheritFromIAMRole` and point
`s3Credentials` at a Secret with `accessKeyId` and `secretAccessKey` keys
instead.

**Restart the instance after adding the annotation.** The IRSA web-identity
token is injected when a pod is created, so a pod that was already running
never receives one:

```bash
kubectl -n supabase delete pod supabase-db-1
```

At `instances: 1` that is a brief outage with no replica to fail over to. Do it
deliberately rather than discovering it. Confirm the token arrived:

```bash
kubectl -n supabase get pod supabase-db-1 \
  -o jsonpath='{.spec.containers[0].env[?(@.name=="AWS_ROLE_ARN")].name}{"\n"}'
```

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
## Step 9 - Point inres at it

**Purpose:** the application reads all of this from one Secret. Both the Go API
and the agent verify HS256 tokens against `supabase_jwt_secret`; if it does not
match what GoTrue signs with, every request is rejected as unauthenticated and
the chat WebSocket closes with `4001`.

Derive the values from the cluster rather than retyping them. Nearly every
failure in this runbook has been a value that looked right and did not match:

```bash
kubectl create namespace inres

PG_PASSWORD="$(kubectl -n supabase get secret supabase-db  -o jsonpath='{.data.password}'   | base64 -d)"
JWT_SECRET="$( kubectl -n supabase get secret supabase-jwt -o jsonpath='{.data.secret}'     | base64 -d)"
ANON_KEY="$(   kubectl -n supabase get secret supabase-jwt -o jsonpath='{.data.anonKey}'    | base64 -d)"
SERVICE_KEY="$(kubectl -n supabase get secret supabase-jwt -o jsonpath='{.data.serviceKey}' | base64 -d)"
```

```bash
cat > config.yaml <<YAML
database_url: "postgresql://postgres:${PG_PASSWORD}@supabase-db-rw.supabase.svc.cluster.local:5432/postgres?sslmode=disable"
port: "8080"

inres_api_url: "http://inres-api:8080"
inres_web_url: "http://inres-web:3000"
backend_url: "http://inres-api:8080"
data_dir: "./data"

# The release name is part of the service name: installing the Supabase chart
# as "supabase" produces supabase-supabase-kong.
supabase_url: "http://supabase-supabase-kong.supabase.svc.cluster.local:8000"
public_supabase_url: "https://inres.example.com"
supabase_anon_key: "${ANON_KEY}"
supabase_service_role_key: "${SERVICE_KEY}"
supabase_jwt_secret: "${JWT_SECRET}"

# Exactly one Anthropic credential. An API key takes precedence wherever it is
# found, so it must be genuinely empty for an OAuth token to be used - see
# step 10.
anthropic_api_key: ""

ai_agent:
  model: "claude-opus-5"
  require_tool_approval: true
  max_concurrent_cli: 8
YAML

kubectl -n inres create secret generic inres-secrets --from-file=config.yaml
rm -f config.yaml
```

Delete the file afterwards. It holds the JWT secret and the database password
in plaintext, in whatever directory you happened to be in.

`public_supabase_url` is the browser-facing address and is the one value you
cannot derive - it must be whatever hostname users reach. For a port-forwarded
smoke test, the internal address works.

**Check:** every value is populated. An unexpanded variable produces an empty
string, not an error:

```bash
kubectl -n inres get secret inres-secrets -o jsonpath='{.data.config\.yaml}' | base64 -d \
  | grep -E "^(supabase_anon_key|supabase_service_role_key|supabase_jwt_secret):" \
  | sed -E 's/: "(.{0,6}).*"/: \1... /'
```

Three non-empty prefixes. Any bare `: ...` means that shell variable was empty
when the heredoc ran.

---

## Step 10 - Install inres

Only now.

**Chart version and app version move independently.** The chart is at `0.x`;
the images it deploys are at `1.x`. Passing an app version where a chart
version belongs fails with a bare `not found`. List what exists:

```bash
TOK=$(curl -s "https://ghcr.io/token?scope=repository%3A<owner>%2Fcharts%2Finres%3Apull&service=ghcr.io" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOK" \
  "https://ghcr.io/v2/<owner>/charts/inres/tags/list" | python3 -m json.tool
```

If you authenticate with an OAuth token rather than an API key, the agent needs
it in the environment. Store it separately - it does not belong in the config
secret, which is mounted as a file:

```bash
claude setup-token
read -rs TOKEN && kubectl -n inres create secret generic inres-claude-oauth \
  --from-literal=token="$TOKEN" && unset TOKEN
```

```yaml
# inres-values.yaml
components:
  ai:
    # Helm replaces lists, it does not merge them. Every entry the chart
    # already sets has to be repeated here or it is lost - including
    # inres_CONFIG_PATH, without which the agent cannot find its config.
    env:
      - name: PORT
        value: "8002"
      - name: HOST
        value: "0.0.0.0"
      - name: USER_WORKSPACES_DIR
        value: "/app/workspaces"
      - name: inres_CONFIG_PATH
        value: "/etc/inres/config.yaml"
      - name: CLAUDE_CODE_OAUTH_TOKEN
        valueFrom:
          secretKeyRef:
            name: inres-claude-oauth
            key: token

  # Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN; crash-loops without them.
  slack-worker:
    replicas: 0

  # cmd/server already runs the escalation workers in-process, so a separate
  # worker Deployment puts two uncoordinated consumers on the same queue.
  worker:
    replicas: 0
```

```bash
helm upgrade --install inres oci://ghcr.io/<owner>/charts/inres \
  -n inres --version 0.4.0 -f inres-values.yaml
```

Keep `inres-values.yaml`. Every later `helm upgrade` needs it, and omitting it
silently drops the OAuth token and the config path together.

Leave `migration.enabled` at its default of `false` - step 7 already applied
every migration, and the job needs three secret keys this runbook does not
create.

**On upgrades:** if you have scaled or edited anything through `kubectl` or
k9s, server-side apply records that tool as the owner of those fields and helm
refuses to overwrite them. Add `--force-conflicts` to take ownership back. On
Helm 4, `--force` means `--force-replace`, which is a different thing and is
rejected outright alongside server-side apply.

**Check:**

```bash
kubectl -n inres get pods            # all Running and Ready
kubectl -n inres logs deploy/inres-ai | head -30
```

The agent logs its resolved settings at startup. Two lines prove the config
secret was read rather than defaulted: `PGMQ queue 'incident_analysis_queue'
ready` means `database_url` parsed and connected, and `Agent CLI concurrency
limit set to N` echoes your `ai_agent.max_concurrent_cli`.

Confirm the credential actually reached the container, since a healthy pod
proves nothing here - the agent starts fine without one and fails on the first
message:

```bash
kubectl -n inres exec deploy/inres-ai -- sh -c \
  'printf "oauth len: "; printf "%s" "$CLAUDE_CODE_OAUTH_TOKEN" | wc -c
   printf "api key:   "; test -n "$ANTHROPIC_API_KEY" && echo "set - shadows OAuth" || echo "unset"'
```

Then open the UI, sign in with the user from step 6, and ask the assistant a
question - that one request crosses the frontend, Kong, the agent, the Claude
CLI, the incident tools, the Go API and Postgres, which is the fastest way to
confirm the whole chain.

The agent's CORS allowlist defaults to `localhost:3000` and `localhost:8000`,
so port-forward to port 8000 for this test; any other hostname needs
`AI_ALLOWED_ORIGINS` set.

---

## Notes on what this leaves out

**Realtime may not be worth operating.** It is the fiddliest component - its
own role, a logical replication slot, its own failure modes - and the Go API
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

**Every step here has now been run end to end** on a live EKS cluster, from an
empty namespace to an agent answering over the WebSocket. The version of this
document that preceded that run read perfectly well and failed at eleven
separate points, so a few notes on what that changed:

| | |
|---|---|
| Verified | The Postgres image, the bootstrap SQL, the Supabase chart values, the migration ordering, backups with a completed base backup and `ContinuousArchiving: True`, and the full inres install. |
| Verified | Kong's auth chain end to end - a valid `anon` key returns 200, a missing or wrong one returns 401. This is worth testing explicitly; empty JWT keys pass every earlier check. |
| Environment-specific | The IAM policies and IRSA trust are written for EKS. The shape is right; the ARNs, OIDC issuer and bucket are yours to fill in. |
| Still unproven | Restore. Backups reported success and the objects are in S3, but no recovery has been performed from them. |

The failures clustered in two places, and both are worth slowing down for:
**step 5**, where a missing role surfaces several steps later as an
authentication error, and **step 6**, where a wrong field name is not an error
at all - Helm ignores unknown values, so the chart quietly keeps its defaults.

A pattern worth carrying into any step not covered here: check the thing
itself, not its proxy. A `Running` pod does not mean a migrated schema, an
existing schema does not mean it has tables, a populated secret key does not
mean a non-empty value, and `barmanObjectStore` reporting success does not mean
a backup you can restore.
