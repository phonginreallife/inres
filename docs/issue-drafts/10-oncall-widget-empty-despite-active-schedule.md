## Symptom

The dashboard's **On-Call Now** card shows the empty state:

> No active on-call schedules
> Set up on-call ->

while the group it should be reading has an active schedule. On the AppSec-SRE
group page (`escalation`, `Active`, method `parallel`), the On-Call Schedule tab
shows `1 scheduler - 52 shifts`, and the timeline's "now" marker falls inside a
`phong.le` shift on both the Original and Final rows.

Two screens in the same app disagree about whether anyone is on call.

## Cause

Not yet identified, but the code narrows it to two possibilities.

`components/dashboard/OnCallStatus.js:56` fetches per group and normalises the
response like this:

```js
const currentOnCall = await apiClient.getCurrentOnCall(group.id, { org_id: currentOrg.id });
return {
  group_id: group.id,
  group_name: group.name,
  current_oncall: currentOnCall?.current_oncall || currentOnCall,
  message: currentOnCall?.message
};
```

then filters with `results.filter(r => r.current_oncall)` (line 77).

When nobody is on call, `handlers/oncall.go:63` returns
`{"current_oncall": null, "message": "No one is currently on-call for this group"}`.
The `|| currentOnCall` fallback turns that into the **whole response object**,
which is truthy, so the group survives the filter and renders a (garbage) card.

So the empty state is unreachable via the "no one on call" path. Reaching it
requires one of:

1. `getGroups({ org_id })` returned `[]` - the early return at line 50. Note the
   widget passes only `org_id`, never `project_id`, while the rest of the
   dashboard sends both. If group listing is project-scoped, AppSec-SRE may not
   come back here.
2. Every `getCurrentOnCall` call threw. `lib/api.js:43` throws on any non-2xx, and
   the per-group `catch` (line 65) swallows it into `current_oncall: null`, which
   the filter then drops. `GET /groups/:id/schedules/current` returns 500 on any
   query error (`handlers/oncall.go:56`).

Worth checking as part of this: the two screens read different tables.
`OnCallService.GetCurrentOnCallUser` (`services/oncall.go:66`) queries `shifts`
directly:

```sql
WHERE os.group_id = $1
  AND os.is_active = true
  AND NOW() BETWEEN os.start_time AND os.end_time
```

with `JOIN users u ON os.user_id = u.id`. The group page renders from
`GET /groups/:id/scheduler-timelines`, which goes through the scheduler services
and the `schedulers` table. If scheduler-generated shifts land with
`is_active = false`, or with a `user_id` that has no matching `users` row, the
timeline draws them and this query returns `ErrNoRows`.

## Impact

The dashboard is the first screen after login and the one place that answers
"who is on call right now". It currently says nobody is, on an org that has an
active rotation. That is the same answer it would give for a genuinely
unconfigured install, so there is nothing to distinguish a real gap in coverage
from this bug.

## Diagnosis

Run against the group to split cases 1 and 2 from the table mismatch:

```sql
SELECT s.id, s.user_id, s.is_active, s.start_time, s.end_time,
       u.id AS user_row, u.email
FROM shifts s
LEFT JOIN users u ON s.user_id = u.id
WHERE s.group_id = '<appsec-sre group id>'
  AND NOW() BETWEEN s.start_time AND s.end_time;
```

- No rows -> the scheduler is not materialising shifts, or the times are wrong.
- Rows with `is_active = false` -> the `is_active` filter is the cause.
- Rows with `user_row IS NULL` -> the `users` join drops them.
- Rows that look correct -> the failure is in the widget (case 1 or 2); check the
  browser network tab for the `/groups` and `/schedules/current` responses.

## Fix

Depends on the above. Regardless of root cause, two things in the widget should
change:

- Drop the `|| currentOnCall` fallback at `OnCallStatus.js:60`. It cannot produce
  a valid shift and only ever masks a null response as a truthy one.
- Stop swallowing request failures into the same shape as "nobody on call"
  (line 65). A group whose lookup errored should surface as an error, not as
  silence. Right now a 500 and an empty rotation are indistinguishable on screen.

## Test

Needs a test that a group with a currently-active shift appears in the widget's
result set, and a second asserting that a `{current_oncall: null}` response
produces the empty state rather than a truthy entry.
