# CloudNativePG-compatible Postgres for self-hosted inres.
#
# Built on CloudNativePG's own image so the operator's expectations - Debian
# layout, uid 26, barman-cloud for backups - hold without patching. Supabase's
# official image cannot be used: it is Nix-based, with postgres under
# /nix/var/nix/profiles, which CNPG does not understand.
#
# The -bookworm tag is deliberate. The bare :17 tag is Debian 11, whose
# security suite reached end-of-life on 2026-08-31; its .deb files now 404 and
# archive.debian.org has no bullseye-security Release file, so nothing can be
# apt-installed into it.
#
# Only pgmq is added. The migrations also CREATE EXTENSION pg_net,
# supabase_vault, pg_graphql and vector, but nothing in this codebase uses
# them - they came from 20250925091841_remote_schema.sql, a dump of a hosted
# Supabase project. pgmq is different: the Go API and the slack worker call
# pgmq.send/read/delete directly, so the queues stop working without it.
#
# pgmq is pure SQL (PGXS, no C or Rust), so "build" only renders and copies
# .sql and .control files. There is no shared object and therefore no ABI
# coupling to the base image.
ARG PG_MAJOR=17
FROM ghcr.io/cloudnative-pg/postgresql:${PG_MAJOR}-bookworm AS build

ARG PG_MAJOR=17
ARG PGMQ_VERSION=v1.13.0

USER root
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential ca-certificates git postgresql-server-dev-${PG_MAJOR}; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    git clone --depth 1 --branch ${PGMQ_VERSION} https://github.com/pgmq/pgmq.git /src/pgmq; \
    cd /src/pgmq/pgmq-extension; \
    make; \
    make install

# --- runtime: the extension files only, none of the build toolchain
FROM ghcr.io/cloudnative-pg/postgresql:${PG_MAJOR}-bookworm
ARG PG_MAJOR=17

COPY --from=build /usr/share/postgresql/${PG_MAJOR}/extension/pgmq* \
                  /usr/share/postgresql/${PG_MAJOR}/extension/

USER 26
