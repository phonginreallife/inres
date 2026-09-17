---
type: protocol
title: Agent Streaming and WebSocket Event Contract
description: How the Claude Agent SDK message stream is translated into the WebSocket events the InRes browser client consumes, and the invariants — no duplicate text, no subagent noise, exactly one terminal event — that keep the UI consistent.
tags: [ai-agent, websocket, streaming, protocol, events, frontend-contract]
sources:
  - id: openwiki-source-dd49ab36cb3e010f4fbdd495
    resource: repo://frontend/inres/src/hooks/useClaudeWebSocket.js
  - id: openwiki-source-535125c0b1b372f818344929
    resource: repo://server/agent/session/events.py
  - id: openwiki-source-11c4cad098f7c53154745d97
    resource: repo://server/agent/session/translate.py
  - id: openwiki-source-2b7ba9dd6019af15399e3a02
    resource: repo://server/agent/tests/test_translate.py
generated: { by: "claude-code", at: "2026-09-17T11:09:25.960Z" }
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T11:09:25.960Z
---

# Agent Streaming and WebSocket Event Contract

Two modules own the wire between the agent and the browser:

- **`session/translate.py`** decides *what happened* — a pure function over the
  SDK message stream plus a mutable `TurnState`, with no I/O, no SDK client and
  no asyncio. Everything that can be got wrong about the stream is decided here,
  so it can be tested exhaustively against synthetic message sequences.
- **`session/events.py`** decides *what goes on the wire* — every frame the
  agent pushes is built here, so the contract lives in one place instead of
  being spelled out at each emit site.

The live consumer is
[`useClaudeWebSocket.js`](../frontend/web-application.md). Where the frontend
and the SDK disagree about a shape, **the frontend wins**.

Related: [Session architecture](../ai-agent/session-architecture.md) ·
[Web application](../frontend/web-application.md)

---

## What the SDK produces per turn

```
SystemMessage(subtype="init")   session id
StreamEvent * N                 raw Anthropic events — token deltas
AssistantMessage * M            complete messages; M > 1 when tools are used
UserMessage * M-1               tool results, despite the "user" role
ResultMessage                   session id, cost, usage; ends the turn
```

`translate()` dispatches on message type and returns zero or more events. An
unknown message type is logged and ignored rather than killing the turn, because
the CLI may add message types in a version bump. A translation failure is caught
and returns an empty list for the same reason.

---

## The duplicate-text problem

`StreamEvent` deltas and the `AssistantMessage` that follows carry **the same
text**. Emitting both duplicates the whole reply; emitting only the deltas loses
text whenever the CLI skips partials.

The resolution: **stream the deltas, treat the `AssistantMessage` as
authoritative, and emit only the difference.** `_reconcile_text` handles the
three cases:

| Case | Behaviour |
|---|---|
| Deltas add up to exactly the final text | Emit nothing (the normal case) |
| Nothing was streamed (partials off) | Emit the whole message in one frame — degraded, not broken |
| Genuine divergence | Emit nothing, and log loudly |

Divergence suppresses the re-emit because **duplicating text is worse than
dropping a correction**, and it is logged at warning level because it means an
assumption about the CLI no longer holds. Either way, `assistant_text`
accumulates the authoritative final text.

### Two buffers, not one

`TurnState` keeps `assistant_text` (messages the SDK has completed) separate
from `stream_buf` (the message still arriving). An interrupt lands between the
two, and `text_for_persistence` — their concatenation — is what survives it, so
an interrupted turn keeps the partial answer the user actually read.

`message_start` clears only `stream_buf`; whatever it held has already been
folded into `assistant_text` by the preceding `AssistantMessage`. A tool-using
turn produces several assistant messages, and the reply the user saw is their
concatenation.

---

## Subagent output suppression

When Claude delegates to a subagent, the subagent's output arrives with
`parent_tool_use_id` set. It is inner monologue, not the answer, and merging it
produces a reply interleaved with work-in-progress.

Every handler that can see it drops it: `_stream_event` discards subagent
deltas, `_assistant` discards subagent assistant messages, and `_user` discards
a subagent's tool results.

---

## Delta kinds

Within `content_block_delta`, only two delta kinds are displayable:

- **`text_delta`** → appends to `stream_buf` and emits a `delta` event. Empty
  text emits nothing.
- **`thinking_delta`** → accumulates into `thinking_buf` and emits a `thinking`
  event carrying **the running total**, because the frontend *replaces* its
  thinking buffer on each event rather than appending.

`signature_delta` (a thinking signature) and `input_json_delta` (partial,
unparseable tool arguments) are ignored. So are `content_block_start`,
`content_block_stop`, `message_delta` and `message_stop`.

A `ThinkingBlock` on a completed `AssistantMessage` is emitted **only when
nothing was streamed** — otherwise the thinking deltas already covered it and
re-emitting would rewind the UI to a stale snapshot.

---

## Tool events

`ToolUseBlock` produces a `tool_use` event and appends to **two** accumulators:
`st.tool_calls` for logging and audit correlation, and `st.tool_events` for
replay. A `TodoWrite` call additionally emits a `todo_update` so the UI can
render a checklist.

### Two accumulators, two purposes

`tool_events` records tool activity **in the order it happened**, so the
transcript can be replayed with its tool cards intact. The comment states the
failure it prevents: without it a reloaded conversation shows the prose and
silently drops every tool the agent ran — usually the part worth reviewing. The
persister writes these as messages; see
[session architecture](../ai-agent/session-architecture.md).

A `ToolResultBlock` is recorded with the **transport-truncated** text rather
than the raw payload, and the reason is stated inline: a replayed transcript
should match what was on screen, and a multi-megabyte tool result does not
belong in the message table.

`tool_use` carries its payload **twice on purpose**: `useClaudeWebSocket.js`
reads `data.content` and JSON-parses it, while the flat `id`/`name`/`input` keys
are the SDK's shape and what any new consumer would reach for. Sending both
costs a few bytes and keeps either style working. `permission_request` does the
same thing with `input_data` and `tool_input`, because the frontend reads
`data.input_data || data.tool_input`.

Tool results arrive on `UserMessage`s despite the "user" role. A plain-string
`UserMessage` is the CLI echoing our own prompt back and is ignored; only list
content is scanned for `ToolResultBlock`s.

### Truncation

Tool output can run to megabytes — `Bash`, `Grep`, log queries. Past
`MAX_TOOL_RESULT_CHARS` (8000), `tool_result` sends a truncated copy with a
`\n... [N characters truncated]` marker and sets `truncated: true` on the event.
The **full** text is still available for persistence; only the transported copy
is cut. Structured content (a list of blocks) is flattened to text first, taking
each item's `text` field when present.

---

## Serialisation safety

Two rules hold for everything in `events.py`:

1. **Payloads must be JSON-serialisable.** `websocket.send_json` raises on
   anything else, and the sender task treats a raise as fatal — a single bad
   frame would silence the socket for the rest of the session. `jsonable()` is
   the guard, applied to every value originating outside our code: tool inputs,
   tool results, permission suggestions.
2. **Terminal events are emitted by `ChatSession`, never by the translator**, so
   exactly one is sent per turn.

`jsonable()` handles dataclasses (such as the SDK's `PermissionUpdate`), dicts,
sequences and pydantic models (`model_dump`, as used by MCP), and degrades
unknown objects to `repr` — a slightly ugly frame beats a dead socket. A depth
guard at 8 levels prevents cycles from hanging the coercion.

---

## Terminal-event exclusivity

`_result` captures `cost_usd`, `usage`, `num_turns` and the session id, sets
`st.error` when the result is an error, and **returns no events**. The comment
is explicit: *the terminal event belongs to ChatSession*.

`ChatSession._finish()` emits exactly one of `complete`, `error` or
`interrupted` from the turn's `finally` block. That is what guarantees the UI's
spinner stops exactly once, on every path through a turn.

One recovery case lives in `_result`: if no assistant text streamed but the run
produced a result string, that string becomes the assistant text, so the turn is
not recorded as empty. It never overwrites text that did stream.

---

## The event catalogue

| Event | Meaning |
|---|---|
| `delta` | One increment of assistant text |
| `thinking` | Reasoning text — **running total**, not an increment |
| `tool_use` | A tool call (flat and nested shapes) |
| `tool_result` | A tool's output, possibly `truncated` |
| `todo_update` | Task-list state |
| `complete` / `error` / `interrupted` | Terminal — exactly one per turn |
| `session_init` | The Claude session id, once the CLI reports it |
| `processing` | Work started — covers reconnect latency after an idle park |
| `model_changed` | Active model, with `pending` when queued behind a running turn |
| `history_cleared` | Context dropped; new conversation id |
| `ping` | Keepalive |
| `permission_request` | Approve a tool call |
| `permission_timeout` | Nobody answered; the chip can clear |

`model_changed` carries `pending` so the UI can say the switch takes effect next
turn, rather than implying the in-flight answer came from the new model.

---

## Tested invariants

`tests/test_translate.py` is where the contract is pinned, and it covers each
hazard on this page: tokens stream without duplicating on the final message;
only the untransmitted tail is emitted; the whole message goes out when no
partials arrived; divergence suppresses the re-emit but keeps the final text;
text accumulates across multiple assistant messages; `message_start` resets only
the in-flight buffer; subagent deltas, assistant messages and user messages are
dropped; thinking deltas send the running total and a thinking block is emitted
only when nothing streamed; `signature_delta` and `input_json_delta` are
ignored; `tool_use` carries both shapes; large tool results are truncated and
structured content is flattened; result metadata is captured without emitting;
result text is used only when nothing streamed; partial text survives an
interrupted turn; unknown and malformed messages do not raise; and
non-serialisable tool input and cyclic structures are coerced safely.
