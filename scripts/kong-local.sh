#!/usr/bin/env bash
# Start/stop local Kong that proxies to host services (Next :3000, Go :8080, agent :8002).
# Usage: ./scripts/kong-local.sh up -d | down | logs -f
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/deploy/docker"
exec docker compose -f docker-compose.kong-local.yaml "$@"
