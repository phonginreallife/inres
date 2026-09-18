# Postgres image for self-hosted inres

CloudNativePG-compatible Postgres 17 with `pgmq`.

## Why this exists

Two constraints meet here:

- **Supabase's image cannot be used with CloudNativePG.** `supabase/postgres`
  is Nix-based (`/nix/var/nix/profiles/default/bin/postgres`), while CNPG
  expects the Debian layout, uid 26 and barman-cloud.
- **CNPG's image does not ship `pgmq`**, which the Go API and the Slack worker
  call directly (`pgmq.send` / `read` / `delete`). Without it the escalation and
  notification queues do not exist.

Everything else the migrations declare — `pg_net`, `supabase_vault`,
`pg_graphql`, `vector` — is unused by this codebase. Those `CREATE EXTENSION`
statements arrived with `20250925091841_remote_schema.sql`, a dump of a hosted
Supabase project. `vector` in particular is a red herring: the full-text search
migration uses the built-in `tsvector`.

## Build

```bash
docker build -t ghcr.io/<owner>/inres-postgres:17-pgmq deploy/postgres
docker push  ghcr.io/<owner>/inres-postgres:17-pgmq
```

Build for the cluster's architecture — EKS on Graviton needs `linux/arm64`,
everything else `linux/amd64`:

```bash
docker buildx build --platform linux/amd64 -t <image> --push deploy/postgres
```

## Verify

```bash
docker run -d --name pgmq-test -e POSTGRES_PASSWORD=test \
  -e PGDATA=/tmp/pgdata --user 26 <image>
docker exec pgmq-test psql -U postgres -c "CREATE EXTENSION pgmq CASCADE;" \
  -c "SELECT pgmq.create('smoke');" \
  -c "SELECT pgmq.send('smoke', '{\"hello\":\"world\"}');" \
  -c "SELECT message FROM pgmq.read('smoke', 5, 1);"
```

## Notes

`pgmq` is pure SQL (PGXS — no C, no Rust), so the build only renders and
copies `.sql` and `.control` files. There is no shared object, and therefore no
ABI coupling between the build stage and the runtime image.

The `-bookworm` base is required, not cosmetic. CNPG's bare `:17` tag is
Debian 11, whose security suite reached end-of-life on 2026-08-31; its `.deb`
files now return 404 and `archive.debian.org` carries no `bullseye-security`
Release file, so nothing can be `apt-get install`ed into it.
