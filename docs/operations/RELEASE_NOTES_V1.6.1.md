# v1.6.1 — Release Notes (Draft, Not Published)

**Status: In preparation.** v1.6.1 has not been tagged, packaged, published, or deployed. Further fixes and enhancements may be added to this branch before final review. This document must be finalized with the complete inventory and verified test evidence before publishing.

## Changes since v1.6.0

| Area | Planned change | Impact |
|---|---|---|
| HA monitoring diagnostics | During failover, tolerate up to two consecutive checks with no promoted HA Operator. On the third consecutive such reading, emit a throttled error; conflicting promoted nodes, loss of quorum and exporter failures remain immediate errors. | Reduces expected transient Syslog ERROR noise without hiding sustained or unsafe HA status. No change to HA failover/failback detection, owner-only notifications, or Docker container monitoring. |
| MariaDB helper configuration clarity | Remove the unverified MariaDB syntax-check Docker label rule from Python defaults, installer-generated configuration and the example template. Retain optional explicit label-based exclusions and the existing narrow built-in dump-helper exception. | Avoids documenting a label not observed on the temporary MariaDB helper; no change to the verified built-in exit-137 suppression. Existing customer YAML remains preserved on upgrades, including older rules. |

**Additional changes:** To be updated as development PRs are integrated.

## Compatibility and operations

No new service, port, endpoint or privilege is required. Default HA polling remains 60 seconds; the existing 2-valid-poll failover confirmation and 5-minute repeated-error rate limit are retained. Newly generated configs omit the optional `ignored_container_events` key; the reference template defaults it to `[]`. The event-label mechanism is still available for explicit, verified labels. The strict built-in MariaDB dump-helper exception stays enabled and unchanged. Installer upgrades **preserve existing customer YAML**, so they do not automatically remove old label entries.

## Validation (pending)

- Unit/static tests: pending development PR and current-head CI evidence.
- Release PR CI and GitHub-hosted isolated Docker smoke: pending. Never run destructive Docker tests on operational/shared hosts.
- Full change inventory, acceptance, package/digest validation and rollback review: pending final release decision.

**No release action:** Do not tag `v1.6.1` or publish a package until the final reviewed release PR is merged into `main`, all required checks are green, and explicit go-ahead is given.
