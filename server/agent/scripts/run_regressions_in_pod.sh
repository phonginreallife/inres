#!/usr/bin/env bash
# Run the new regression tests inside the AI pod against an overlay copy of
# /app, so the running application's files are never modified.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
POD=$(kubectl -n inres get pod -l app.kubernetes.io/component=ai \
        -o jsonpath='{.items[0].metadata.name}')
echo "pod: $POD"
kubectl -n inres exec "$POD" -- sh -c \
  'rm -rf /tmp/apptest && mkdir -p /tmp/apptest && cd /app && \
   tar --exclude=./workspaces --exclude=./__pycache__ -cf - . | tar -xf - -C /tmp/apptest && echo overlay-ready'
kubectl cp server/agent/session/config.py        "inres/$POD:/tmp/apptest/session/config.py"
kubectl cp server/agent/tests/test_regressions.py "inres/$POD:/tmp/apptest/tests/test_regressions.py"
kubectl -n inres exec "$POD" -- sh -c \
  'cd /tmp/apptest && python3 -m pytest tests/test_regressions.py -q -rxX 2>&1 | tail -20'
