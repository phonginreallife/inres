## Symptom

Acknowledging an incident in PagerDuty does not acknowledge it in InRes. The
incident stays `triggered` and keeps escalating.

Observed on production at 15:28:14 UTC on 2026-09-22, for PagerDuty incident
#13320 (`Your check 'MDCloud - Multiscanning - MDaaS - HIGH (USW2)' is failing!`):

```
incident.triggered     -> SUCCESS: Created incident 7778026e-4dbc-4b61-aef9-83bda57d82e8
incident.acknowledged  -> Found existing incident 7778026e, skipping duplicate creation
                       -> Incrementing alert count for incident 7778026e
incident.acknowledged  -> Found existing incident 7778026e, skipping duplicate creation
                       -> Incrementing alert count for incident 7778026e
incident.resolved      -> SUCCESS: Resolved incident 7778026e
```

Trigger and resolve both land. The two acknowledgements only bump the alert
count; the incident's status is untouched.

## Cause

Acknowledgement has no representation anywhere in the alert pipeline.

`handlers/webhook_types.go:494` flattens it into `firing` at parse time:

```go
} else if strings.Contains(eventType, "acknowledged") || dataStatus == "acknowledged" {
    status = "firing" // Still active, just acknowledged
}
```

`ProcessedAlert.Status` is documented as `firing, resolved` only
(`webhook_types.go:26`), so there is no third value to carry.

`handlers/webhook.go:704` then has nowhere to dispatch it:

```go
switch alert.Status {
case "firing":
    return h.routeAlertToCreateIncident(integration, alert)
case "resolved":
    return h.routeAlertToResolveIncident(integration, alert)
default:
    log.Printf("WARNING: Unknown alert status %s, treating as firing", alert.Status)
    return h.routeAlertToCreateIncident(integration, alert)
}
```

So an `incident.acknowledged` event takes the `firing` branch, hits the
fingerprint dedupe in `routeAlertToCreateIncident` (`webhook.go:720`), increments
the alert count and returns. `IncidentService.AcknowledgeIncident`
(`services/incident.go:985`) already exists and is never called from this path.

## Impact

An engineer who acknowledges in PagerDuty is still paged by InRes. The incident
remains `triggered`, so the escalation worker keeps considering it and will
escalate it once an escalation policy is attached. The two systems disagree about
who is handling the incident, which is the specific failure on-call tooling is
supposed to prevent.

This is currently masked because no escalation policy is attached to the
PagerDuty integration (see the service/escalation mapping gap). It will start
paging people the moment that is configured.

## Fix

Carry acknowledgement through the pipeline as a first-class status:

1. Add `acknowledged` to `ProcessedAlert.Status` and stop collapsing it in
   `mapPagerDutyStatus` (`webhook_types.go:494`).
2. Add an `acknowledged` branch to `routeAlert` (`webhook.go:704`) that resolves
   the incident by fingerprint and calls
   `IncidentService.AcknowledgeIncident(id, systemUserID, note)`, matching the
   shape of `routeAlertToResolveIncident`.
3. Use `db.GetSystemUserBySource(integration.Type)` for the actor, as the resolve
   path does, so the timeline attributes it to the integration rather than a
   real user.
4. Keep the alert-count increment: a repeated ack is still a duplicate signal.

Worth deciding as part of this: whether an ack on an incident InRes has already
resolved should reopen it, or be ignored. The resolve path currently logs and
skips when no incident is found (`webhook.go:764`); ack should be consistent.

Other sources send the same shape and will benefit: Datadog, Grafana and
Coralogix all map into the same two-state `ProcessedAlert.Status`.

## Test

`handlers/webhook_pagerduty_test.go` already has an `"Acknowledged Incident"`
case (line 82) that asserts the current flattening. It needs inverting to expect
`acknowledged`, plus a routing-level test that an `incident.acknowledged` event
for an existing fingerprint leaves the incident in `acknowledged`, not
`triggered`.

## Related

Outbound direction is a separate draft: acknowledging in InRes does not
acknowledge in PagerDuty.
