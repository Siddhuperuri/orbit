# Incident Response Runbook

## Severity levels

SEV1 is a customer-facing outage or confirmed data loss. SEV2 is a degraded service with a workaround. SEV3 is a minor defect with no customer impact.

## Paging

The on-call engineer is paged through PagerDuty and must acknowledge within five minutes. If there is no acknowledgement, the page escalates to the secondary on-call and then to the engineering director.

## Incident commander

For SEV1 and SEV2 the first responder becomes incident commander, opens a bridge, and keeps a written timeline. The commander may hand over explicitly but never implicitly.

## Postmortem

A blameless postmortem is written within five working days of every SEV1 and SEV2, with owners and dates for each follow-up action.
