#!/usr/bin/env bash
# Create every draft in this directory as a GitHub issue.
#
# The account that debugged these is an Enterprise Managed User and cannot open
# issues on a personal repo, so this is split out to run under whichever account
# owns phonginreallife/inres:
#
#   gh auth switch --user <personal-account>
#   ./docs/issue-drafts/create-all.sh
set -euo pipefail
cd "$(dirname "$0")"

declare -a TITLES=(
  "AI chat hangs indefinitely: turn produces no output and no error"
  "Agent incident tools cannot reach the Go API (inres_API_URL unset)"
  "require_tool_approval=false is fatal: bypassPermissions rejected as root"
  "AuditService.log() raises AttributeError when start() has not run"
  "Configured redis_url is ignored, so AI rate limiting is silently inert"
  "slack-worker in CrashLoopBackOff: slack_bot_token/slack_app_token missing"
  "config loader logs the first 30 characters of every exported secret"
  "PagerDuty incident.acknowledged does not acknowledge the InRes incident"
  "PagerDuty state sync is one-way: acting in InRes does not update PagerDuty"
  "Dashboard On-Call Now shows no schedules while the group has an active rotation"
  "Let users choose the agent's permission mode (Manual/Edit/Plan/Auto) from the UI"
)
declare -a FILES=(
  01-chat-hangs-no-output.md
  02-incident-tools-cannot-reach-api.md
  03-bypass-permissions-fatal-as-root.md
  04-audit-log-raises-before-start.md
  05-redis-url-config-ignored.md
  06-slack-worker-crashloop.md
  07-loader-logs-secret-prefixes.md
  08-pagerduty-ack-does-not-acknowledge.md
  09-pagerduty-state-sync-is-one-way.md
  10-oncall-widget-empty-despite-active-schedule.md
  11-agent-permission-modes-in-ui.md
)
declare -a LABELS=(
  bug
  bug
  bug
  bug
  bug
  bug
  bug
  bug
  enhancement
  bug
  enhancement
)

for i in "${!FILES[@]}"; do
  echo "creating: ${TITLES[$i]}"
  gh issue create --title "${TITLES[$i]}" --label "${LABELS[$i]}" --body-file "${FILES[$i]}"
done
