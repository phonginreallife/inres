---
type: subsystem
title: Web Application
description: The Next.js App Router frontend — how it bootstraps its Supabase client from the API, propagates auth tokens and org/project scope into every request, receives realtime updates, and transforms schedules for the on-call timeline.
tags: [frontend, nextjs, react, supabase, websocket, realtime, scheduling]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-e3d331e68a59983aaf21981e
    resource: repo://frontend/inres/src/app/layout.js
  - id: openwiki-source-715db83f00cdd70d97fc778f
    resource: repo://frontend/inres/src/contexts/AuthContext.tsx
  - id: openwiki-source-afc45e3a3cd7ee37095d0d47
    resource: repo://frontend/inres/src/contexts/NotificationContext.tsx
  - id: openwiki-source-47bb0e61a283f94adc6a52a9
    resource: repo://frontend/inres/src/contexts/OrgContext.tsx
  - id: openwiki-source-dd49ab36cb3e010f4fbdd495
    resource: repo://frontend/inres/src/hooks/useClaudeWebSocket.js
  - id: openwiki-source-709d2a3a5de219621de577dd
    resource: repo://frontend/inres/src/hooks/useRealtimeRefresh.js
  - id: openwiki-source-e72171fb3ebbbd185eaba247
    resource: repo://frontend/inres/src/lib/api.js
  - id: openwiki-source-0de55396da439d514fd3f537
    resource: repo://frontend/inres/src/lib/supabase.js
  - id: openwiki-source-9c03cda18ec5ac8c48b9a634
    resource: repo://frontend/inres/src/services/__tests__/scheduleTransformer.test.js
  - id: openwiki-source-d53811a36ae3a39a9c52d192
    resource: repo://frontend/inres/src/services/GAP_FIX_SUMMARY.md
  - id: openwiki-source-937bb38601b6d70d2818ad52
    resource: repo://frontend/inres/src/services/scheduleTransformer.js
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Web Application

`frontend/inres` is a Next.js 16 / React 19 application using the App Router.
It talks to two backends — the Go API for REST and the Python agent over a
WebSocket — and receives push updates through Supabase Realtime.

Related: [Streaming protocol](../ai-agent/streaming-protocol.md) ·
[Tenancy and authorization](../concepts/tenancy-and-authorization.md) ·
[On-call scheduling](../workflows/oncall-scheduling.md)

---

## Route structure

`src/app/` mirrors the product surface directly: `dashboard`, `incidents`,
`alerts`, `monitors`, `groups`, `integrations`, `organizations`, `projects`,
`profile`, `audit`, `onboarding`, `agent-config`, `ai-agent`, and the auth
routes `login`, `signup`, `auth` and `shared`.

`src/components/` is organised by the same feature boundaries, with a shared
`ui/` directory.

---

## The provider stack

`layout.js` nests providers in a specific order, and the order is load-bearing:

```
ThemeProvider
└── AuthProvider
    └── SidebarProvider
        └── OrgProvider
            └── NotificationProvider
                └── AuthWrapper  → Sidebar, MobileNav, MainContent
```

`OrgProvider` sits **inside** `AuthProvider` because it needs a session before
it can fetch organizations, and `NotificationProvider` sits inside `OrgProvider`
because its realtime subscription is scoped to the current organization.

The root `<html>` carries `suppressHydrationWarning` and a default `dark` class,
the usual accommodation for a theme applied before React hydrates. The app also
declares PWA metadata — a manifest, Apple web-app settings and a viewport with
`viewportFit: 'cover'` — and ships a `PWAInstallPrompt`.

---

## Configuration bootstrap

The frontend does **not** read Supabase credentials from build-time environment
variables in the normal path. `lib/supabase.js` fetches them at runtime from the
API's `/env` endpoint, falling back to `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` only if that call fails.

This is what lets one prebuilt container image be deployed against different
Supabase projects — the image does not need rebuilding per environment.

The client is a guarded singleton: `configPromise` deduplicates concurrent
config fetches, an `isInitializing` flag makes late callers wait rather than
construct a second client, and the instance is re-checked after the await. More
than one Supabase client in a page causes duplicate auth listeners and
subscriptions, so the guarding matters.

---

## Authentication and token propagation

`AuthContext` owns the session. On mount it reads the existing session,
**validates it by fetching the user**, and clears it from storage if invalid —
specifically handling stale-token errors such as a bad `session_id` claim, which
a plain presence check would miss. It then subscribes to `onAuthStateChange`.

The single most important line is `apiClient.setToken(session.access_token)`.
`APIClient` holds the token on the instance and attaches
`Authorization: Bearer <token>` to every request, so no call site handles auth
itself. Sign-out calls `setToken(null)`.

Token changes are compared against a ref before triggering updates, so a session
refresh that yields the same token does not cause a re-render cascade or a
component remount.

---

## Org and project scope

`OrgContext` holds organizations and projects, the current selection for each,
and separate `loading` and `isRefreshing` flags — the distinction exists so a
background refresh does not unmount children the way an initial load does.

Selections persist in `localStorage`. On load the saved id is restored if it is
still in the fetched list, otherwise the first entry is selected and stored.
Switching organizations **clears the stored project**, because a project id from
one org is meaningless in another.

Scope reaches the backend through `APIClient._buildReBACParams`, which appends
`org_id` and `project_id` as query parameters. `org_id` is mandatory for tenant
isolation and `project_id` optional — the mirror image of `GetReBACFilters` on
the Go side (see
[tenancy and authorization](../concepts/tenancy-and-authorization.md)).

---

## The API client

`lib/api.js` is a single `APIClient` class over `fetch`. Its `request` method
handles what every call would otherwise repeat:

- **Timeouts** via `AbortController`, 15 seconds by default, surfaced as a
  `Request timeout` error rather than a hang.
- **Error extraction** — on a non-OK response it parses the body for `error` and
  `details` to produce a readable message instead of a bare status code.
- **Empty responses** — `204` or zero-length bodies return `{ success: true }`
  rather than failing to parse.

Two base URLs are held: `NEXT_PUBLIC_API_URL` (default `/api`) and
`NEXT_PUBLIC_AI_API_URL` (default `/ai`). The relative defaults are what let
Kong route both through one origin in production.

---

## Realtime notifications

`NotificationContext` subscribes to a Supabase Realtime channel named
`org-notifications-${currentOrg.id}`, so tenants are separated at the channel
level. It keeps the channel in a ref so the app can also *broadcast* on it, and
unsubscribes on cleanup or org change.

A comment records a deliberate choice: **`postgres_changes` subscriptions were
removed in favour of API-driven broadcast only, to prevent duplicates.** Both
mechanisms would have delivered the same event twice.

`useRealtimeRefresh` sits on top, letting a page refetch when a relevant event
arrives. It debounces (300 ms default), tracks the last processed notification
id so one event fires a callback once, and holds callbacks in a ref to avoid
stale closures.

---

## The agent chat UI

`useClaudeWebSocket` is the client half of the
[streaming protocol](../ai-agent/streaming-protocol.md). It connects to
`/ws/chat`, passing the auth token, `org_id`, `project_id` and any
`conversation_id` as **query parameters** — a browser `WebSocket` cannot set
request headers, so the token travels in the URL.

The token is held in a ref rather than a closure variable, so a reconnect uses
the current token rather than the one captured when the effect first ran.

Reconnection is automatic with a bounded attempt count and a fixed 3-second
delay. Two cases suppress it: a normal closure (code 1000) and an explicit
intentional disconnect. On reconnect the hook resumes the matching Claude
session, so conversation context survives a dropped socket.

The hook's message handler is a `switch` over the event types the agent emits —
`delta`, `thinking`, `tool_use`, `tool_result`, `permission_request`,
`todo_update`, `complete`, `error`, `interrupted`, `model_changed`,
`history_cleared`, `session_init`, `processing`, `ping` — appending tokens to
the in-flight assistant message as they arrive. A comment notes that the agent
streams tokens over `/ws/chat` itself, with no separate streaming endpoint.

---

## Schedule transformation

`services/scheduleTransformer.js` converts between the UI's rotation
configuration and the backend's shift representation. It is the most
logic-dense part of the frontend, and the only part with a unit test
(`__tests__/scheduleTransformer.test.js`).

It maps shift-length names to day counts (`one_day` → 1, `one_week` → 7,
`two_weeks` → 14, `one_month` → 30, defaulting to 7), validates that dates parse
and that the end follows the start, and exposes several transformation entry
points for single-shift, simple, rotating and yearly-rotation schedules —
`generateRotationShifts` projecting 52 weeks ahead by default.

### The coverage-gap fix

`GAP_FIX_SUMMARY.md` documents a real bug worth knowing about. The original
`generateRotationShifts` computed each shift's start by adding a fixed number of
days to the rotation start, so every shift began at the same time of day. When
the handoff time differed from the start time, that left uncovered windows:

```
Alice: 2025-01-01T09:00Z → 2025-01-08T02:00Z
Bob:   2025-01-08T09:00Z → 2025-01-15T02:00Z
                 ↑ 7 hours with nobody on call
```

The fix tracks the previous shift's end time and starts the next shift there,
producing continuous coverage. This is exactly the kind of off-by-one that is
invisible in a UI and severe in production — an alert firing in that window
would route to nobody.

---

## Notable dependencies

`vis-timeline` and `vis-data` render the on-call timeline; `chart.js` with
`react-chartjs-2` draws incident trends; `react-markdown` with `remark-gfm`,
`rehype-highlight` and `rehype-starry-night` render agent replies;
`react-hot-toast` provides notifications; and `qrcode` generates the mobile
device-pairing codes described in
[authentication and identity](../concepts/authentication-and-identity.md).
