# HA Monitoring — Design Decisions and Verification

**Purpose:** Explain why the optional active-node change alert works as it does, what it detects, and what its limits are. This is an engineering reference, not an installation procedure or a release checklist. Operator settings are in [Installation and Operations](../operations/INSTALLATION_AND_OPERATIONS.md#44-health-checks-exclusions-and-ha).

## Decision summary

| Decision | Why |
|---|---|
| Observe the **promoted/active HA Operator** (`HAOperatorRep-Res`), not the Pacemaker Designated Controller (DC) | The DC is the Pacemaker coordinator; it can be on the standby node and is **not** evidence of the active CyberController role |
| Use the existing **local Pacemaker HA exporter** at `http://127.0.0.1:9664/metrics` | No additional daemon, privileged host command, container, firewall opening, cross-node API call, or exporter is needed |
| Run HA polling in a **separate thread and HTTP session** from Docker polling | Slow or blocked Docker API calls must not delay HA observations; HA exporter failures must not interrupt container monitoring |
| Require **quorum, a successful Pacemaker scrape, one local node and exactly one promoted owner** | Missing or conflicting telemetry is not proof of a role change; fail closed instead of inventing a failover |
| Require **two consecutive valid observations** of a new active node | Avoid reporting transient/promoted-resource ambiguity while the cluster is transitioning |
| **Only the newly active node** emits the alert | Watchdogs run on both nodes; duplicate notifications from old and new active nodes would be misleading |
| Persist the confirmed active node using the existing state file | Survive watchdog restarts without replaying an old role change or creating a second state store |
| Reuse the existing notification channels and SNMP MIB | Treat HA as another monitored event without adding a new alert transport or breaking container notifications |

## How the monitor decides

1. Disabled by default. When enabled, each node reads its **own** exporter using the normal `check_interval_seconds` (default 60 seconds). Requests have a 3-second timeout, a 256 KiB response cap, and permit only loopback HTTP `/metrics` URLs with no redirects.
2. It reads `ha_cluster_pacemaker_resources{resource="HAOperatorRep-Res",role="promoted",status="active"} == 1` to identify the active owner and `ha_cluster_corosync_member_votes{local="true"}` to identify the local node. The Pacemaker scrape-success and quorum metrics must both be 1.
3. The **first valid reading** establishes a silent baseline. An active-node change must appear in **two valid consecutive checks** before it is confirmed.
4. On a confirmed transition A → B, both nodes can update their saved baseline, but **only the watchdog local to B** issues one `WARNING` `ha-failover` event with previous/new node details.
5. Invalid/unavailable metrics leave the last confirmed baseline unchanged and reset a pending candidate. Exporter errors are logged at most once every five minutes; they never trigger a speculative failover alert.
6. HA state is kept separately from container incidents in `watchdog/state.json`. Restart with monitoring enabled restores that state. Running the watchdog with HA monitoring disabled clears its baseline, so a later re-enable starts silently.
7. During the host reboot grace interval, HA role checks are deferred. HA events are not included in container recovery or reboot-summary logic.

The alert means **a promoted active role was observed to change**. It does **not** identify the cause (planned switchover versus unexpected failover) or guarantee all applications are healthy. The detection timestamp is the observation time, not necessarily the Pacemaker event time.

## Notification and state trade-offs

- Existing Syslog, SMTP, Slack and SNMP delivery paths are reused. The alert is `ha-failover`, not a container crash, and must not produce a fake container recovery.
- SNMP retains the existing notification OID and five var-binds: `cwContainerName=(HA cluster)` and `cwFailureType=ha-failover` distinguish HA events; previous/new owners appear in the summary and details.
- Delivery is **best effort**: the confirmed baseline is persisted before dispatch, and failed delivery is logged but not automatically retried. This avoids duplicates from repeated polls; it does **not** guarantee exactly-once receiver delivery. Failed persistence can allow a duplicate after restart. Adding a durable delivery queue would change the notification architecture and is intentionally out of scope.

## What verification established

| Check | Result / implication |
|---|---|
| Parser, state and failure-mode unit tests | Covered correct promotion/local identity, bad metrics, no quorum, startup silence, two-poll confirmation, failback, disabled/re-enabled baseline, persistence, and notification formatting |
| Docker polling deliberately blocked in a mocked test | The independent HA thread continued; Docker and HA checks do not depend on each other's progress |
| Real two-node **switchover and failback** | Observed one new-owner WARNING per direction and no old-owner duplicate |
| Actual Syslog delivery on both nodes | HA failover and failback records reached the configured Syslog receivers |
| Transient ambiguous exporter reading during a transition | An invalid reading did not cause a false HA event; later valid readings produced the correct notification |

The [release acceptance report](RELEASE_ACCEPTANCE_V1.6.0.md#ha-acceptance-carried-forward-actual-pacemaker-operations) contains the supporting lab results. Future regressions are in [`tests/test_ha_monitor.py`](../../tests/test_ha_monitor.py). These are prior validation results, not evidence that every delivery channel or later code/image version has been validated.

## Limits and lessons retained

- Both HA nodes need a running watchdog, accessible **local** exporter, and configured notification path. No access from one node to the other's exporter is necessary.
- If a surviving node has never learned the earlier active owner, it starts with a silent baseline. The monitor cannot reconstruct older transitions. Fast changes that happen entirely between polls can also be missed.
- Unexpected active-node loss, network partitions, fencing/STONITH and SMTP/Slack/physical-NMS SNMP delivery were **not** demonstrated end to end by the recorded two-node acceptance. Do not infer coverage from successful switchover/failback tests.
- During a planned HA demotion, removal of dependent application containers previously produced separate CRITICAL container alerts. HA role-change detection is intentionally separate from Docker failure detection; suppressing those application events requires a distinct, narrowly scoped rule and its own tests.
- The older native Prometheus HA rule used location-score information that did not reliably prove promotion; its exporter scrape also targeted a different port. Rather than modifying that native monitoring, this feature reads the validated local HA Operator metrics directly.
- No HA control, resource promotion, remediation, Pacemaker writes, cluster configuration changes, or new ports are part of this feature.

**When revisiting the design:** first confirm exporter metric names and HA Operator resource identity on the target CyberController version. Preserve fail-closed checks, independent polling and quiet baseline. Run mocked regression tests and use only a controlled, separate HA test environment for real switchovers; never induce failover in an operational cluster to test documentation.
