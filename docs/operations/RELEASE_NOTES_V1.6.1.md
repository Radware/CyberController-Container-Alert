# v1.6.1 — Release Notes (Draft, Not Published)

**Status: In preparation.** v1.6.1 has not been tagged, packaged, published, or deployed. Further fixes and enhancements may be added to this branch before final review. This document must be finalized with the complete inventory and verified test evidence before publishing.

## Changes since v1.6.0

| Area | Planned change | Impact |
|---|---|---|
| HA monitoring diagnostics | During failover, tolerate up to two consecutive checks with no promoted HA Operator. On the third consecutive such reading, emit a throttled error; conflicting promoted nodes, loss of quorum and exporter failures remain immediate errors. | Reduces expected transient Syslog ERROR noise without hiding sustained or unsafe HA status. No change to HA failover/failback detection, owner-only notifications, or Docker container monitoring. |

**Additional changes:** To be updated as development PRs are integrated.

## Compatibility and operations

No configuration key, new service, port, endpoint or privilege is required by the initial HA diagnostic change. Default HA polling remains 60 seconds; the existing 2-valid-poll failover confirmation and 5-minute repeated-error rate limit are retained.

## Validation (pending)

- Unit/static tests: pending development PR and current-head CI evidence.
- Release PR CI and GitHub-hosted isolated Docker smoke: pending. Never run destructive Docker tests on operational/shared hosts.
- Full change inventory, acceptance, package/digest validation and rollback review: pending final release decision.

**No release action:** Do not tag `v1.6.1` or publish a package until the final reviewed release PR is merged into `main`, all required checks are green, and explicit go-ahead is given.
