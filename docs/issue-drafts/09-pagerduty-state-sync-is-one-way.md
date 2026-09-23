## Symptom

State flows from PagerDuty into InRes and never back. Acknowledging or resolving
an incident in InRes leaves the PagerDuty incident untouched, so it keeps paging
its own on-call.

## Cause

There is no outbound PagerDuty client. The only PagerDuty types in the codebase
are inbound webhook payload structs (`handlers/webhook_types.go:160-190`), and
nothing in `server/` references `api.pagerduty.com` or holds a PagerDuty API
token.

`IncidentService.AcknowledgeIncident` (`services/incident.go:985`) and
`ResolveIncident` update local state and fire local notifications. Neither has a
hook for telling the originating integration what happened.

## Impact

For any incident that originated in PagerDuty, the two systems diverge as soon as
someone acts in InRes:

- Acknowledge in InRes -> PagerDuty still `triggered`, still escalating, still
  paging its own rotation.
- Resolve in InRes -> PagerDuty incident stays open indefinitely.

Responders have to do everything twice, and whichever system they forget is the
one that pages them at 3am. It also undercuts the point of routing PagerDuty into
InRes at all: InRes cannot be the place people work incidents if acting there has
no effect on the source.

## Proposal

Bidirectional sync for the triggered/acknowledged/resolved lifecycle.

**Inbound** (PagerDuty -> InRes) is nearly complete: trigger and resolve work,
acknowledge is missing. See the acknowledgement draft.

**Outbound** (InRes -> PagerDuty) needs:

1. **Credentials per integration.** A PagerDuty REST API token stored against the
   integration record, not a global env var, since one install can point at
   several PagerDuty accounts. Needs a migration on the integrations table.
2. **A client** wrapping `PUT /incidents` on `api.pagerduty.com` with the
   `From` header, to set an incident's status to `acknowledged` or `resolved`.
   The PagerDuty incident id is already captured on the way in: it lands in the
   incident labels as `incident_id` (e.g. `Q0AWHAIED8725J`), which is also the
   fingerprint.
3. **Hooks** in `AcknowledgeIncident` and `ResolveIncident` that, when the
   incident came from a PagerDuty integration, enqueue an outbound sync rather
   than calling the API inline. PGMQ is already the pattern for this kind of work
   (`escalation_queue`, `slack_notification_queue`); a third queue keeps a slow or
   failing PagerDuty API from blocking the local state change.
4. **Loop suppression.** Our `PUT` causes PagerDuty to emit
   `incident.acknowledged`/`incident.resolved` straight back to our webhook. The
   inbound handler must recognise a change it caused and not re-apply it.
   PagerDuty's webhook payload carries `agent` on the event, so an event whose
   agent is the API user we authenticated as can be dropped. Worth confirming
   against a real payload before relying on it.

## Open questions

- Should outbound sync be opt-in per integration? Some installs will want InRes
  read-only against PagerDuty.
- What happens when the outbound call fails after local state changed? Retry via
  the queue, surface the divergence on the incident, or both.
- Does resolving in InRes need a resolution note pushed to PagerDuty, or is
  status enough?

## Test

Needs a fake PagerDuty API in the Go tests asserting that acknowledging a
PagerDuty-sourced incident issues one `PUT /incidents` with status
`acknowledged`, and that the echoed webhook it would produce is suppressed rather
than applied.

## Related

The inbound acknowledgement gap is a separate draft and should land first -
outbound sync on top of a lifecycle that cannot represent `acknowledged` would
only be able to sync resolves.
