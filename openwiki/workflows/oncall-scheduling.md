---
type: workflow
title: On-Call Scheduling and Rotations
description: How InRes models schedulers, shifts, rotation cycles and overrides, why override resolution lives in a database view rather than application code, and why a second optimized scheduler implementation exists alongside the original.
tags: [oncall, scheduling, shifts, rotations, overrides, postgres-views, performance]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-02711a3496408c7e206ec7df
    resource: repo://server/api/cmd/server/main.go
  - id: openwiki-source-0d58800e4a6061a65eeff1bb
    resource: repo://server/api/cmd/worker/main.go
  - id: openwiki-source-25ab6a4ff4a3f11a759408ea
    resource: repo://server/api/handlers/optimized_scheduler.go
  - id: openwiki-source-3959ed3b6b53a23e909f8e0f
    resource: repo://server/api/internal/background/incident.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-0a0fe3844e6ccd74f5a10cba
    resource: repo://server/api/services/oncall.go
  - id: openwiki-source-22043d9d55753fbf52cee9ea
    resource: repo://server/api/services/optimized_scheduler_service.go
  - id: openwiki-source-f4af11adefd8a57b2012467d
    resource: repo://server/api/services/override.go
  - id: openwiki-source-a66d4ec20e4fe73455e50b6e
    resource: repo://server/api/services/rotation.go
  - id: openwiki-source-f2d4b17e989d39f9989c6146
    resource: repo://server/api/services/scheduler_service.go
  - id: openwiki-source-26fa97b9b723550b716b40be
    resource: repo://supabase/migrations/20251017080005_add_scheduler_composite_indexes.sql
  - id: openwiki-source-2496dff3225cab4e508a0f60
    resource: repo://supabase/migrations/20251017163508_create_effective_shifts_view.sql
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# On-Call Scheduling and Rotations

Scheduling exists to answer one question at the moment an incident fires:
**who is on call right now?** Everything else — rotations, swaps, vacation
overrides — is machinery for making that answer correct.

Related: [Escalation and notifications](../workflows/escalation-and-notifications.md) ·
[Web application](../frontend/web-application.md) ·
[Data model and migrations](../operations/data-and-migrations.md)

---

## The model

| Entity | Role |
|---|---|
| **Group** | The team that owns services and schedules |
| **Scheduler** | A named schedule within a group; a group may have several |
| **Shift** | One person on call for one interval |
| **Rotation cycle** | Generates shifts by cycling members |
| **Override** | Temporarily substitutes a different person into a shift |

A shift carries a `schedule_scope` (`group` or `service`) and an optional
`service_id`, which is what allows one service to have its own on-call rota
distinct from the group's.

---

## `effective_shifts` — the centre of the design

The most important decision in this subsystem is that **override resolution
lives in a database view, not in Go**.

`effective_shifts` joins `shifts` to `schedulers`, `users` and
`schedule_overrides`, and exposes:

- **`effective_user_id`** = `COALESCE(so.new_user_id, s.user_id)` — the person
  actually on call.
- `original_user_id` — who was scheduled.
- `is_overridden`, and `is_full_override` (true only when the override's window
  fully covers the shift).
- Both users' name, email, team and phone, plus `user_name`/`user_email`
  coalesced to the **effective** person, which the view's own comment directs
  callers to use for assignments and notifications.

The override join is where precedence is decided:

```sql
LEFT JOIN schedule_overrides so ON s.id = so.original_schedule_id
    AND so.is_active = true
    AND CURRENT_TIMESTAMP BETWEEN so.override_start_time AND so.override_end_time
```

Three conditions must hold: the override targets this shift, it is active, and
**the current moment falls inside its window**. An override that has expired
stops applying automatically — nothing needs to deactivate it. The view also
filters to `s.is_active AND sc.is_active`, so deactivating a scheduler removes
its shifts from every on-call answer at once.

### Why this placement matters

Querying `effective_shifts` gives override-correct results to every consumer
without any of them knowing overrides exist. The escalation worker's
`escalateToScheduler` selects `effective_user_id` from the view and is
override-aware for free (see
[escalation and notifications](../workflows/escalation-and-notifications.md)).

The cost is the mirror image: **any code that queries `shifts` directly bypasses
overrides.** That is a real inconsistency in the tree —
`SchedulerService.getCurrentSchedule` and
`OnCallService.GetCurrentOnCallUser` read `shifts` and join `users` themselves,
so paths using them return the originally scheduled person even when an override
is active.

### Time handling

The view uses `CURRENT_TIMESTAMP` and callers use `NOW()`, so the comparison
happens in the database rather than against an application clock. Both Go
entrypoints set `SET TIME ZONE 'UTC'` on connect, so those functions evaluate in
UTC regardless of server locale, and the frontend converts for display.

---

## Overrides

`CreateOverride` validates before writing:

- `override_type` must be `temporary`, `permanent` or `emergency`; anything else
  is **silently coerced to `temporary`** rather than rejected.
- The end time must not precede the start time.
- The original shift must exist and be active — `group_id` and the original user
  are read from it rather than trusted from the request.
- **The replacement must differ from the original user**, rejected with an
  explicit error. A no-op override would otherwise look like coverage while
  changing nothing.

A failure to look up the replacement's name for the response is logged as a
warning and the override still succeeds — display metadata is not worth failing
a coverage change over.

---

## Rotations and swaps

`RotationService` creates rotation cycles that generate shifts by cycling group
members. `GetRotationPreview` projects forward a given number of weeks so the UI
can show who will be on call before the schedule is committed, and
`GetCurrentRotationMember` answers for now. `DeactivateRotationCycle` retires a
cycle without deleting history.

`OnCallService.SwapSchedules` exchanges two shifts. `executeScheduleSwap` runs in
a transaction and also calls `updateRotationCyclesForSwap` and
`swapUsersInRotationCycle` — so a swap updates **both** the shifts and the
underlying rotation membership. Swapping only the shifts would leave the
rotation to regenerate the original assignment later and silently undo the swap.

`checkOverlappingSchedules` detects conflicting shifts in a group, excluding a
given id so an edit does not conflict with itself.

---

## Service-scoped schedules

`GetEffectiveScheduleForService` implements a two-tier fallback: look for a
schedule scoped to the specific service, and fall back to the group-wide
schedule if there is none. A team can therefore run one general rota and add a
dedicated one only for the services that need it, without duplicating coverage
for the rest.

---

## Two scheduler implementations

`SchedulerService.CreateSchedulerWithShifts` and
`OptimizedSchedulerService.CreateSchedulerWithShiftsOptimized` both exist, and
the router registers both:

| Route | Implementation |
|---|---|
| `POST /groups/:id/schedulers/with-shifts` | Optimized (**default**) |
| `POST /groups/:id/schedulers/with-shifts-legacy` | Original (fallback) |

The optimized path is the default and the legacy one is explicitly labelled a
fallback, so the pair is a migration safety net rather than a permanent fork.

### How they differ

Both create a scheduler and its shifts. The optimized version applies two
labelled optimizations inside a single transaction:

1. **Unique-name generation in one query** instead of repeated existence checks.
2. **Batch shift insertion** in a single statement instead of one insert per
   shift.

The second dominates: creating a year of weekly shifts is ~52 round trips in the
original and one in the optimized version.

### Measuring rather than asserting

Two endpoints exist specifically to justify the choice:

- `GET /groups/:id/schedulers/stats` — scheduler performance statistics.
- `POST /groups/:id/schedulers/benchmark` — runs both implementations over a
  configurable iteration count and **compares them directly**.

Shipping a benchmark endpoint alongside the two implementations is what lets the
legacy path be retired on evidence.

### Index support

`20251017080005_add_scheduler_composite_indexes.sql` adds partial composite
indexes covering the real query shapes: shifts by scheduler, group or user with
time filtering, overlap detection, and service-scoped lookup. Each carries a
`WHERE is_active = true` predicate, so the indexes cover only rows that can be
returned — smaller and faster than full indexes, since inactive shifts are never
part of an answer.

---

## Reading schedules

`GetGroupSchedulerTimelines` assembles the timeline the dashboard renders,
`GetSchedulesByScope` filters by group or service scope, and
`GetUpcomingSchedules` returns a forward window.
`GetOrCreateDefaultScheduler` lazily creates a group's default scheduler, so a
group need not be configured before it can be scheduled.

On the client, `services/scheduleTransformer.js` converts between the UI's
rotation configuration and this shift representation — including the
coverage-gap fix described in
[web application](../frontend/web-application.md), where shifts were left with
uncovered windows between them.
