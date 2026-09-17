---
type: subsystem
title: "Agent Tool Approval, Zero Trust and Audit"
description: How InRes gates the AI agent's tool use behind human approval without deadlocking the WebSocket, how the zero-trust socket verifies every signed message independently, and how audit events are sanitized and batch-written.
tags: [ai-agent, security, tool-approval, zero-trust, audit, permissions, replay-protection]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-866643a38207b09bec7ab2f8
    resource: repo://server/agent/audit/hooks.py
  - id: openwiki-source-4de478136b28810501644b3a
    resource: repo://server/agent/audit/service.py
  - id: openwiki-source-b81fff3f8071bf6dce13b998
    resource: repo://server/agent/security/verifier.py
  - id: openwiki-source-0a2f8796ac884ea9c3c96ed6
    resource: repo://server/agent/session/permissions.py
  - id: openwiki-source-7695c6cd21bd114170cc633e
    resource: repo://server/agent/session/session.py
  - id: openwiki-source-5124fcedf7b0938f58af1e14
    resource: repo://server/agent/tests/test_permissions.py
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Agent Tool Approval, Zero Trust and Audit

An agent that can run `Bash` against production infrastructure needs three
separate guarantees, and InRes implements each in its own module:

1. **A human decides** what runs — `session/permissions.py`.
2. **Every message is proven authentic**, not just the first one —
   `security/verifier.py`.
3. **Everything that happened is recorded**, with secrets stripped —
   `audit/`.

Related: [Session architecture](../ai-agent/session-architecture.md) ·
[Authentication and identity](../concepts/authentication-and-identity.md)

---

## Human-in-the-loop tool approval

### The round trip

`PermissionBroker` sits between the Claude Agent SDK and the browser. The SDK
calls `can_use_tool` before running any tool it is not already permitted to run,
and the broker turns that into a user-visible question: it mints a request id,
creates a future, pushes a `permission_request` event onto the output queue, and
awaits the future. The WebSocket receive loop resolves it when the user clicks
Approve or Deny.

Three outcomes are possible, and none of them kills the turn:

- **Approved** → `PermissionResultAllow()`.
- **Denied** → `PermissionResultDeny(message=..., interrupt=False)`.
- **Timed out** after `permission_timeout_s` (300 s default) → a
  `permission_timeout` event clears the UI prompt, and the SDK receives a denial
  explaining that nobody answered.

`interrupt=False` is deliberate on every denial path. Killing the turn outright
would leave the user with nothing; instead the model can explain itself or try a
different route.

### The deadlock-freedom argument

While a request is pending, four things are waiting:

```
SDK reader task  -> the future
session runner   -> the (empty) SDK message stream
sender task      -> the (empty) output queue
WS receive loop  -> the ASGI receive channel
```

The property that makes this safe is that **nothing the SDK owns feeds the ASGI
receive channel**. No edge points back into the receive loop, so the wait graph
stays acyclic, and the loop can always read the `permission_response` that
breaks the cycle.

Four things would break that property, and the code is shaped to avoid all four:

1. **Never await an SDK control call from the receive loop while a request is
   pending.** `interrupt()`, `set_model()` and `disconnect()` each await a
   control *response* that only the SDK's reader task can deliver — and that
   task may be parked inside `can_use_tool`. `ChatSession.interrupt()` therefore
   calls `broker.deny_all("Interrupted by user")` *before* touching
   `client.interrupt()`.
2. **Never await the turn task from the receive loop.** A turn blocked on a
   pending approval never finishes.
3. **Never bound the output queue.** A stalled sender would block the request
   before it was ever transmitted, so nobody could answer it.
4. **Never shield the future.** Cancellation has to propagate; orderly shutdown
   goes through `deny_all`.

`resolve()` and `deny_all()` are synchronous for exactly this reason — the
receive loop must never block while holding the only key.

### Why denial rather than cancellation on shutdown

`deny_all()` resolves every outstanding future with `False` rather than
cancelling it. A cancelled callback can leave the CLI waiting on a control
response that never arrives, and `disconnect()` then hangs until its own
timeout. `close()` sets a closed flag — so new requests are refused immediately
rather than parking forever — and then denies whatever is outstanding.

### The allowlist short-circuit

Requests the user has already blessed skip the round trip entirely.
`is_allowed()` is consulted first, and its patterns follow Claude Code's own
syntax:

| Pattern | Matches |
|---|---|
| `Read` | any use of the tool |
| `Bash(kubectl get:*)` | uses whose primary argument starts with the prefix |
| `Bash(ls -la)` | one exact invocation |

Prefix matching needs to know *which* argument describes what a tool is about to
do. `_PRIMARY_ARG` maps each known tool to that argument — `command` for `Bash`,
`file_path` for `Read`/`Write`/`Edit`, `pattern` for `Grep`/`Glob`, `url` for
`WebFetch` — falling back to the first string value in the input for anything
unmapped. Non-dict tool input degrades to an empty string rather than raising.

The allowlist is seeded from the user's stored pre-approvals (see
[extensibility](../ai-agent/extensibility.md)) and extended at runtime by the
UI's "always allow" button via `add_to_allowlist`, which is idempotent.

### Tested invariants

`tests/test_permissions.py` pins the behaviour that the deadlock argument
depends on: a denial does not kill the turn; a timeout emits the clearing event;
a late response to an expired request is rejected; an unknown request id is
rejected; a double resolve is rejected; `deny_all` releases waiters and is a
no-op when idle; `close` releases waiters and refuses new requests; and
cancelling a waiter does not leak a pending entry.

---

## The zero-trust socket

`/ws/secure/chat` exists for clients — notably mobile — that cannot be trusted
to hold a Supabase JWT. It replaces "authenticate once, then trust the
connection" with per-message cryptographic verification.

### Two key layers

**Instance → device.** The self-hosted instance holds an ECDSA P-256 keypair.
It issues a `DeviceCertificate` binding a device's public key to a user id, an
instance id, a permission list and an expiry, signed with the instance key.

**Device → message.** The device holds an Ed25519 keypair. Every message it
sends carries a signature over the payload.

`verify_certificate()` checks expiry, resolves the instance public key from an
in-memory cache — fetching it from the backend and registering it on a miss —
and verifies the ECDSA signature over a canonical JSON rendering of the
certificate fields. One wrinkle is handled explicitly: the Go issuer produces
raw `R || S` signatures, while the Python `cryptography` library expects DER, so
a 64-byte signature is converted with `encode_dss_signature` before
verification.

### What every message must satisfy

`verify_message()` applies eight checks in order, and any failure rejects the
message:

1. The session resolves, from cache or from the `agent_sessions` table.
2. Both a payload and a signature are present.
3. The payload's `cert_id` matches the session's certificate.
4. The timestamp is within `MESSAGE_TIMESTAMP_WINDOW` (60 s) of now.
5. A nonce is present.
6. The nonce has not been seen before — a database lookup, not a memory one.
7. The Ed25519 signature verifies against the canonical JSON of the payload.
8. The message type is permitted: `chat_message` requires the `chat`
   permission, `tool_approval` requires `tools`.

Only after the signature verifies is the nonce persisted, so a message that
fails verification cannot burn a nonce.

### Canonical JSON is a compatibility contract

Signatures are computed over `json.dumps(data, sort_keys=True,
separators=(',', ':'), ensure_ascii=False)`. The `ensure_ascii=False` is
load-bearing: it matches Dart's `jsonEncode()` on the mobile client. Without it,
non-ASCII text — the code cites Vietnamese, where `"có"` would become
`"có"` — would serialize differently on the two sides and every signature
over such a message would fail.

### Durability across restarts

Sessions, nonces and instance keys are persisted to PostgreSQL, with the
in-memory caches treated purely as a performance optimization and the database
as the source of truth. A restarted agent process therefore still rejects
replays and still honours live sessions. Sessions expire after
`SESSION_EXPIRY_HOURS` (7 days) — deliberately longer than certificates, since
the certificate is the shorter-lived credential — and `revoke_session()`
deactivates the row rather than only clearing the cache.

---

## Audit logging

### Categories and event types

Audit events follow an AWS CloudTrail-style `category.action` naming scheme
across four categories:

| Category | Examples |
|---|---|
| `session` | `session.created`, `session.authenticated`, `session.ended`, `session.revoked` |
| `chat` | `chat.message_sent`, `chat.response_received`, `chat.conversation_created` |
| `tool` | `tool.requested`, `tool.approved`, `tool.denied`, `tool.executed`, `tool.error` |
| `security` | `security.auth_failed`, `security.rate_limited`, `security.signature_invalid`, `security.nonce_replay`, `security.certificate_expired` |

Each event carries an outcome status of `success`, `failure` or `pending`.

### Where events come from

Tool execution is instrumented through the SDK's own hook system rather than
threaded through business logic: `PreToolUse` records the start time and tool
input keyed by `tool_use_id`, and `PostToolUse` correlates against that context
to log completion or error with a duration. Because hooks only fire *after*
permission is granted, `tool.requested`, `tool.approved` and `tool.denied` are
logged from the permission callbacks instead — the hook path would never see a
denied tool.

### Sanitization

`DataSanitizer` runs over `request_params`, `response_data` and `metadata`
before anything is queued. It works on two axes:

- **By key**: any key containing `password`, `token`, `secret`, `api_key`,
  `authorization`, `credit_card`, `ssn` and similar is replaced with
  `[REDACTED:<n> chars]`, preserving length but not content.
- **By value**: regexes replace JWT bearer tokens, `sk-` API keys, GitHub PATs,
  Slack tokens and email addresses with labelled placeholders.

Recursion is depth-bounded (`[MAX_DEPTH_EXCEEDED]` past 10 levels) and strings
longer than 10 000 characters are truncated with a note of the original length.

Shell-shaped tools get extra treatment: `sanitize_tool_input` additionally
redacts `--password=`, `-p <value>`, `PGPASSWORD=` and
`AWS_SECRET_ACCESS_KEY=` inside the command string, because credentials passed
inline to a command would otherwise survive key-based redaction.

### Async batching

`AuditService` never writes to the database on the request path. Events go onto
a bounded `asyncio.Queue` (10 000 entries by default); a background worker
drains it into a buffer and flushes when either the buffer reaches `batch_size`
(50) or `flush_interval` (5 s) has elapsed — whichever comes first — writing the
batch in one insert.

Failure handling is bounded rather than unbounded: a failed flush puts the
events back in the buffer for retry only while the buffer is below half the max
queue size; past that, the events are counted as dropped. Audit logging degrades
under sustained database failure instead of exhausting memory. `stop()` flushes
the remaining buffer before cancelling the worker, so an orderly shutdown does
not lose queued events.
