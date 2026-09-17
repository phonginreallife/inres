---
type: subsystem
title: AI Agent Session Architecture
description: How the InRes agent holds one Claude Agent SDK client open for the life of a WebSocket — the four concurrent tasks, why a single task must own connect and disconnect, and why turns are serialised rather than cancelled.
tags: [ai-agent, websocket, session, asyncio, lifecycle, streaming, concurrency]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T11:09:25.960Z
sources:
  - id: openwiki-source-7101a6239e312a00b6d9e177
    resource: repo://server/agent/main.py
  - id: openwiki-source-7695c6cd21bd114170cc633e
    resource: repo://server/agent/session/session.py
  - id: openwiki-source-1b3ba3af80313d11aae0b666
    resource: repo://server/agent/tests/test_session.py
  - id: openwiki-source-d9a6ad667cc247baf67de263
    resource: repo://server/agent/ws_chat.py
generated: { by: "claude-code", at: "2026-09-17T11:09:25.960Z" }
---

# AI Agent Session Architecture

The agent service runs one **persistent** Claude Agent SDK client per WebSocket
connection. That single decision shapes everything else in the subsystem.

The design replaced a per-message pattern in which every user message opened a
fresh `ClaudeSDKClient`, discarded its context, and then ran a *second*
inference against the raw Anthropic API purely to stream tokens. Keeping one
client connected fixes both problems at once: context survives between turns,
and `include_partial_messages` yields token-level streaming from the same pass
that runs the tools — no second inference.

Related: [Streaming protocol](../ai-agent/streaming-protocol.md) ·
[Tool approval and security](../ai-agent/security-and-tool-approval.md) ·
[Extensibility](../ai-agent/extensibility.md)

---

## The four tasks

```
UI ◄── delta / tool events ── ChatSession ◄── Claude Agent SDK ──► tools
```

| Task | Responsibility | Must never |
|---|---|---|
| **WS receive loop** | Reads frames, submits `Turn`s, resolves approvals | Touch the SDK client; await a turn |
| **Session runner** | connect → N turns → disconnect; owns the client | — |
| **Sender task** | Drains the output queue to the socket | Block on a full queue (it is unbounded) |
| **SDK internals** | Tasks spawned by `connect()`; may call `can_use_tool` | — |

### Why one task owns connect *and* disconnect

`ClaudeSDKClient.connect()` enters an anyio task group, and **anyio requires the
task that entered a scope to be the one that exits it**. Connecting lazily
inside a per-message task and disconnecting from the WebSocket handler's
`finally` therefore raises `RuntimeError` and leaks the CLI subprocess.

`ChatSession._run()` is the answer: a single long-lived task whose loop is
literally `connect → serve turns → disconnect`, with `_disconnect()` in both the
inner `finally` and the outer one. Nothing else ever calls `connect()` or
`disconnect()`.

### The receive loop stays free

Two rules keep the socket responsive while a turn runs:

- `handle_chat` returns as soon as the turn is queued; all database work happens
  in a separate `persist-turn` task. The loop therefore stays free to read the
  next frame — which may be the approval or interrupt the *running* turn is
  waiting for.
- `handle_permission_response` is synchronous by design. The receive loop holds
  the only key to a parked SDK callback and must not block while holding it.

The output queue is unbounded for the same reason: a bounded queue could block
the permission callback before its request was ever transmitted, and then nobody
could answer it.

---

## Turn serialisation

Turns are pushed onto an `asyncio.Queue` and run one at a time. They are
**serialised, never cancelled**, and the reason is specific to a persistent
client: cancelling a turn mid-stream leaves unread messages queued inside the
client, and those messages then interleave with the next turn's output.

`_serve()` runs turns against one open client until one of four things happens:

1. **Idle timeout** (`idle_timeout_s`) — the CLI subprocess is parked, but
   `_claude_session_id` is kept so the next message resumes.
2. **Reset** — a `None` sentinel with `_reset_requested` clears
   `_claude_session_id`, so the next message starts a fresh conversation.
3. **Tenant change** — `Turn.auth_key` is the `(auth_token, org_id, project_id)`
   triple; when it differs from the connected one the turn is stashed in
   `_carry` and the runner reconnects.
4. **Close**.

### Why a tenant change forces a reconnect

`_apply_tool_context()` binds the auth token, org id and project id for the
incident tools **before** `connect()`, and must run in the runner task. The
tools read context variables, which are copied into each task at creation — so
the SDK's internal tasks inherit whatever was set at connect time, and nothing
set afterwards can reach them. Serving a different tenant therefore requires a
new client, not a mutated one.

### Deferred model switches

`set_model()` applies immediately when idle and is otherwise queued into
`_pending_model`, drained at the top of the next turn. It is deliberately never
awaited mid-turn: `client.set_model()` is a control request whose response only
the SDK's reader task can deliver, and that task may be parked inside a pending
tool approval — the same trap as `interrupt()`. The requested model is validated
against the configured allowlist in `handle_set_model` before it reaches the
CLI, because the value arrives straight off a socket.

---

## Turn execution and the single terminal event

`_run_turn` queries the client, iterates `receive_response()`, and pushes
whatever `translate()` produces onto the output queue, capturing the CLI's
session id as it appears.

Every path out of `_run_turn` — normal completion, cancellation, exception —
lands in `_finish()`, which records the result on the `Turn` and emits **exactly
one** terminal event: `interrupted`, `error`, or `complete`. That single
convergence point is what guarantees the client's spinner always stops. The
translator never emits terminal events itself.

Errors are passed through `sanitize_error_message` before they reach the socket,
so internal details do not leak to the browser.

---

## Interrupts

`interrupt()` is ordered deliberately:

1. `broker.deny_all("Interrupted by user")` — **first**. The SDK's reader task
   may be parked inside `can_use_tool`, and `client.interrupt()` awaits a
   control response that only that task can deliver. Interrupting first would
   deadlock the socket.
2. If nothing is running, emit `interrupted` anyway so the button feels
   responsive, and send no control request the CLI is not expecting.
3. Otherwise call `client.interrupt()` with a 5-second timeout, falling back to
   `_force_restart()` on failure.
4. Arm a watchdog.

The watchdog is the backstop: if the turn is still running `INTERRUPT_GRACE_S`
(10 s) after the interrupt, `_force_restart()` cancels the runner and drops the
subprocess. The session id is kept, so the next message reconnects and resumes,
and `submit()` restarts a dead runner on demand — the session self-heals.

---

## Connection lifecycle details

**Lazy connect.** `start()` creates the runner but does not connect; the runner
blocks on the turn queue and connects only when real work arrives.

**Concurrency ceiling.** A process-wide semaphore (`configure_concurrency`, set
from `max_concurrent_cli` at startup, default 8) caps live CLI subprocesses. The
slot is acquired in `_connect()` and released in `_disconnect()` — including
when connect fails, which is covered by a dedicated test.

**Resume is best-effort.** If `_claude_session_id` is set, the runner tries to
resume; on failure it logs, clears the id and connects fresh. Session
transcripts live on the pod that created them, so a restart or a different
replica invalidates them — and losing history beats refusing the message.

**Shielded disconnect.** `client.disconnect()` is wrapped in
`asyncio.shield` with a 10-second timeout, so a cancelled runner still tears
down the subprocess instead of orphaning it.

**Ordered shutdown.** `ChatConnection.aclose()` releases approvals first, then
closes the session, then drains persister tasks, then stops the heartbeat, then
flushes the sender with a `None` sentinel. Releasing approvals first is what
stops the SDK waiting on a decision that can no longer arrive.

---

## Persistence and conversation identity

`_persist_turn` writes the user message immediately, awaits `turn.done`, then
writes the turn's tool activity, then the assistant reply. Two judgement calls
are encoded there:

- **Interrupted turns are still stored** — the partial answer the user actually
  read is worth keeping.
- **Empty replies are skipped** — a blank row renders as an empty bubble and
  inflates `message_count`.

### Tool activity is persisted too

A `Turn` accumulates `tool_events` in order, and the persister writes each
`tool_use` and `tool_result` as its own message **before** the assistant reply,
so a replayed conversation renders in the order things actually happened:
user → tool_use → tool_result → assistant.

The comment records why this was added: the columns for tool messages had
existed since the table was created, but nothing had ever written them, so
reloading a conversation **silently dropped every tool the agent ran**. A
transcript showed the model's conclusions with no trace of the commands it
executed to reach them — exactly the part an incident review needs.

`is_first` is captured synchronously in `handle_chat` before any await, so two
messages in quick succession cannot both believe they are the first and create
the conversation twice.

`resume_previous` separates two decisions that look like one: adopting a
conversation id and resuming the Claude session. A conversation whose first turn
never produced a session id still has messages on the user's screen, so new
turns must append to it — otherwise the visible transcript and the rows being
written drift apart. Only the model's context is lost, and that is recoverable.

Clearing history mints a **new** conversation id, because reusing the old one
would append to the history the user just asked to forget.

---

## The sender task

`_send_events` drains the queue and writes frames to the socket. A failure is
classified by `_is_disconnect()`: a genuine disconnect (Starlette's
`WebSocketDisconnect`, or a `RuntimeError` complaining about sending after
close) ends the loop, while anything else — an unserialisable frame — drops that
one frame and continues. One bad frame must not silence the socket for the rest
of the session. A heartbeat task emits periodic `ping` events to keep the
connection alive.

---

## Testability

`ChatSession` takes an injectable `client_factory`, and `tests/test_session.py`
supplies a `FakeClient`, so the entire turn loop is tested without the SDK or a
CLI subprocess. The suite pins the behaviours this page describes: lazy connect
and disconnect on close, context persisting across turns on one client, turns
serialised rather than cancelled, interrupt releasing approvals first and
keeping partial text, the client surviving an interrupt to serve the next turn,
failed resume falling back to a fresh session, tenant change forcing a reconnect
while the same tenant reuses the client, the idle timeout parking the subprocess
while keeping the session id, `aclose` being idempotent, `submit` restarting a
dead runner, and the concurrency slot being released when connect fails.
