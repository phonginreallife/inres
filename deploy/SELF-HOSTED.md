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

Each step below ends with a check. Do not move on until it passes — a failure
caught at step 4 costs minutes, the same failure discovered at step 8 looks
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
in step 7, and the keys cannot be regenerated without reissuing every token.

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

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/<owner>/inres-postgres:17-pgmq --push deploy/postgres
```

Match `--platform` to your nodes. Graviton needs `linux/arm64`.

**Check:** `docker manifest inspect ghcr.io/<owner>/inres-postgres:17-pgmq`
returns a manifest for the architecture your nodes run.

---

## Step 4 — Postgres cluster

**Purpose:** the database everything else depends on. Three settings here are
load-bearing:

- `wal_level: logical` and replication slots — Supabase Realtime consumes a
  logical replication slot. Without these it starts, connects, and silently
  never delivers an event.
- `barmanObjectStore` — continuous WAL archiving to S3. This is what makes
  point-in-time recovery possible; adding it later does not backfill.
- `instances: 3` — one primary, two replicas, automatic failover.

```yaml
# postgres-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: supabase-db
  namespace: supabase
spec:
  instances: 3
  imageName: ghcr.io/<owner>/inres-postgres:17-pgmq

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

  backup:
    barmanObjectStore:
      destinationPath: s3://<your-bucket>/supabase-db
      s3Credentials:
        inheritFromIAMRole: true   # EKS: use an IRSA-annotated ServiceAccount
      wal:
        compression: gzip
    retentionPolicy: "30d"

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

## Step 5 — Apply migrations

**Purpose:** creates the schema, the RLS policies and the PGMQ queues. Supabase
services expect some of these to exist before they start.

```bash
kubectl -n supabase port-forward svc/supabase-db-rw 5432:5432 &
export DATABASE_URL="postgresql://postgres:$PG_PASSWORD@localhost:5432/postgres"

for f in supabase/migrations/*.sql; do
  echo "applying $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`20250925091841_remote_schema.sql` will fail on `CREATE EXTENSION pg_net`,
`supabase_vault`, `pg_graphql` and `vector`. That is expected — those are not
installed and nothing in this codebase uses them. They arrived with a dump of a
hosted project. Comment out those four lines, or run with `ON_ERROR_STOP=0` and
confirm afterwards that everything else applied.

**Check:**

```bash
psql "$DATABASE_URL" -c "\dt public.*" | head
psql "$DATABASE_URL" -c "SELECT queue_name FROM pgmq.list_queues();"
```

You should see the inres tables and the queues from
`20250926160648_queue_initial_data.sql`.

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

---

## Step 7 — Point inres at it

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

## Step 8 — Install inres

Only now.

```bash
helm upgrade --install inres oci://ghcr.io/<owner>/charts/inres \
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

**These manifests are not yet exercised end to end.** The Postgres image is
verified — it builds, `CREATE EXTENSION pgmq` succeeds, and a queue round-trips
a message. The Supabase chart values and the CNPG cluster spec are written from
the component requirements and have not been run against a live cluster. Treat
step 4 and step 6 as the two places to slow down.
