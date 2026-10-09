# Optional CyberController HA Active-Node Alert — Design Proposal

**Status:** Implementation approved and completed on feature branch; controlled two-node validation pending
**Date:** 2026-10-09
**Target:** Radware/CyberController-Container-Alert, baseline v1.5.4 (main: a4ddfed)
**Delivery:** One small implementation PR, with release blocked on two-node QA

## 1. Goal and decision

Customer request: alert when CyberController HA changes its active node, including a manual switchover, automatic failover, or failback. The alert should identify the old and new active nodes, not speculate about the cause.

**Decision:** Add one opt-in HA role-change check to the existing Container Watchdog. Read the existing HA exporter directly over host-local HTTP. Reuse the watchdog's polling loop, persistent state, and existing syslog/SMTP/SNMP/Slack delivery. Do not introduce another daemon, Docker container, metric exporter, firewall port, or Pacemaker control capability.

This is an **observed active-node change** alert, not a claim to identify why a failover happened or that all CyberController applications are healthy.

## 2. Verified appliance evidence (read-only inspection)

On the two-node HA environment ha-test-admin-ea-iad1, Pacemaker reports nodes 10.199.0.241 and 10.199.0.242 online. The HA Operator, OpenSearch, PostgreSQL, MariaDB, and Failback promotable resources are currently **Promoted on .241** and **Unpromoted on .242**. The Pacemaker DC is .242; **DC is not the CyberController active node**.

- HA exporter: **ha_cluster_exporter.service**, active on .241, serving port **9664** and reachable at **http://127.0.0.1:9664/metrics** (HTTP 200).
- Promoted role metric: **ha_cluster_pacemaker_resources** includes resource=HAOperatorRep-Res, role=promoted, status=active, node=10.199.0.241, value 1.
- Host identity metric: **ha_cluster_corosync_member_votes** includes local=true, node=10.199.0.241.
- Data-quality signals: **ha_cluster_scrape_success{collector="pacemaker"} = 1** and **ha_cluster_corosync_quorate = 1**.
- Existing Watchdog v1.5.4 runs on .241 in **host network** mode, polls every 60 seconds, has persistent state, and currently delivers syslog.
- Prometheus's native HA target is misconfigured: it scrapes **localhost:9002**, while the exporter serves **9664**. The existing rule **ha_failover_occured** compares per-node location-constraint sum vs average and is not reliable for an actual role change. Correcting the native configuration is a **separate Radware/R&D action**, not part of this feature.
- Standby .242 did **not** accept cross-node connections to 9664/9099/9093. It has not been inspected locally, so its exporter/watchdog installation is **unverified**. Release testing must cover both nodes.

## 3. Small architecture

~~~text
Per CyberController node:
  Existing ha_cluster_exporter :9664/metrics (localhost only)
         |
         | bounded HTTP GET in the existing 60s watchdog poll
         v
  Optional HA role check:
    identify local node + unique promoted HA Operator
    validate quorum / scrape health
    compare with persisted last confirmed active node
    confirm role change on 2 consecutive successful polls
         |
         | only the newly promoted node sends
         v
  Existing watchdog alert dispatcher
    syslog / SMTP / SNMP / Slack

Both HA nodes must run the watchdog and have their own local exporter.
No inter-node polling or new firewall opening is required.
~~~

The check must execute independently of Docker container probes: a Docker API exception must not suppress the HA check, and a failed HA read must not break Docker monitoring. The small HA check runs within the existing process; no worker framework, queue, or additional process is needed.

### Signal selection

**Active owner:** accept exactly one node for which the HA exporter reports:

~~~text
ha_cluster_pacemaker_resources{
  resource="HAOperatorRep-Res",
  role="promoted",
  status="active",
  node="<node-ip>"
} 1
~~~

The check must not use the Pacemaker DC, host virtual IP alone, or location-constraint scores as evidence of an actual promotion. This resource was verified in the current CC version; release testing must confirm the resource name is consistent on the targeted CC versions.

**Sender identity:** derive the local node from the exporter's **ha_cluster_corosync_member_votes{local="true",node=...}** series. Do not guess local identity from WATCHDOG_HOST, container hostname, or the Prometheus instance label.

**Validity:** require a successful HTTP response, valid/parseable metrics, successful Pacemaker collection, quorum present, exactly one promoted HA Operator resource, and a unique local-node identity. Otherwise retain previous state, log a bounded diagnostic warning, and do not announce a completed failover.

### Proposed configuration (disabled by default)

~~~yaml
ha_monitor:
  enabled: false
  exporter_url: http://127.0.0.1:9664/metrics
~~~

Inherit **check_interval_seconds** (60 seconds in current deployments). Keep the implementation constants small: HTTP timeout 3 seconds, a bounded response size, and two consecutive good polls to confirm a new owner. Avoid extra user-configurable knobs unless validation demonstrates a need.

Use only the local exporter in v1. No firewall rule, container exec, host bind mount, database query, REST credential, or Pacemaker write command is required.

## 4. State and alert behavior

Retain a **small optional ha_monitor section in the existing state.json** (backward-compatible with current container alert state). Track the last confirmed active node and the last notified transition. This state must survive watchdog restart, use existing atomic/serialized persistence, and must not be treated as a Docker container entry.

| Observation | Required behavior |
|---|---|
| Disabled, or no HA configured | Existing watchdog behavior unchanged; no HA requests |
| First valid poll without prior HA state | Record baseline silently; no historical or startup alert |
| Same active node | Refresh/retain baseline; no notification |
| Candidate active node changes A -> B | Confirm on next good poll (two consecutive observations) |
| Confirmed A -> B | Update persistent baseline; **only watchdog on B** sends one WARNING |
| Confirmed B -> A (failback) | Same behavior; notify once from A |
| Watchdog restarted after notification | Do not repeat previous transition |
| Exporter timeout, 404/500, invalid/incomplete series, lost quorum, zero/multiple promoted owners | Do not change baseline or claim a completed failover; log a rate-limited local warning |
| Watchdog newly installed after the transition | Baseline silently; historical detection not guaranteed |
| Host reboot/grace window | Respect existing boot-grace behavior; do not mix HA alerts into the container reboot-summary. Reconfirm role after grace |
| Both nodes running watchdog | Both can observe role state, but only the newly promoted node emits the role-change notification |

**Operator notification** (example, not an observed event):

~~~text
[WARNING] CyberController HA active node changed
Previous active: 10.199.0.241
New active:      10.199.0.242
Observed at:     <UTC timestamp>
Evidence:        HAOperatorRep-Res promoted on 10.199.0.242 (Pacemaker exporter)
Next step:       Confirm expected switchover and application health.
~~~

Treat this as an event, **not a persistent container failure**: do not send a spurious "container recovered" alert. Keep the normal container alert subject/fields unchanged. Make only the minimum HA-specific display adjustment needed so email/Slack/syslog/SNMP do not misleadingly label it as a crashed container. Preserve existing notification formats for container alerts.

**Delivery semantics:** one best-effort notification per confirmed observed transition, with state-based deduplication across restarts. Exactly-once delivery to external syslog/SNMP/SMTP cannot be guaranteed. Detection time is an observation timestamp, not necessarily the exact Pacemaker event time.

## 5. Implementation scope — one development PR, after approval

| Existing component | Small change |
|---|---|
| **watchdog.py / DEFAULT_CONFIG** | Optional ha_monitor block, disabled by default |
| **watchdog.py / Watchdog._poll_loop** | Invoke HA poll separately, with its own error handling, on the existing interval |
| **HA reader/transition helper** | Bounded local HTTP read; parse only required metrics; validate; confirm active change; deduplicate |
| **WatchdogState persistence** | Optional HA baseline and notified transition, without breaking existing v1 state files or container recovery |
| **AlertPayload/dispatch_alert** | Reuse channels; ensure HA event is clearly labelled and not folded into container reboot/recovery logic |
| **watchdog-config.yaml.example, README.md** | Add brief opt-in example, behavior, limitations, and both-node setup requirements |
| **tests/test_ha_monitor.py + existing regression suite** | Unit tests, mocks, notification checks |

No changes to **install.sh**, Compose files, Python dependencies, port mappings, Docker privileges, Pacemaker services, or Prometheus are expected. If validation reveals a real need for one, bring it back for design review rather than expanding scope silently.

## 6. Tests and Definition of Done (single implementation increment)

### Automated tests (CI, no live HA action)

- [ ] Feature disabled by default: zero exporter requests and no changes to existing container alerts.
- [ ] Parse metrics with different label orders; select only HAOperatorRep-Res that is active and promoted (value 1), and identify local=true node.
- [ ] Initial valid sample establishes baseline without alert.
- [ ] Stable A -> B across two polls sends **one** HA alert from B; the old node A sends none.
- [ ] B -> A produces one new notification; stable subsequent polls do not repeat.
- [ ] Duplicate/wrong-role resource series, missing local identity, missing Pacemaker-success metric, lost quorum, and zero/multiple promoted owners never create false failover alerts.
- [ ] HTTP timeout, connection refused, malformed metrics and empty response are bounded, leave baseline intact, and do not prevent Docker polling.
- [ ] Persist and restore role and notification deduplication; existing state v1 loads unchanged, and concurrent state writes remain valid.
- [ ] Restart/reboot/grace-window behavior does not generate stale duplicate alerts or contaminate the container reboot-summary.
- [ ] Notification shows previous/new node and UTC observation, is formatted correctly for the configured channels, and does not report fake container failure/recovery.
- [ ] Existing regression tests, Python syntax checks, and Compose/CI checks pass with no new dependencies.

### Controlled two-node validation (manual QA, authorized window)

- [ ] Watchdog and local HA exporter are **running and reachable locally on both nodes**; active role is visible from both local metric endpoints.
- [ ] Confirm normal steady state emits no failover alerts; initial installation/restart remains quiet.
- [ ] With an **explicitly authorized controlled switchover**, verify one notification from newly active node with correct A/B details and no duplicate from old active.
- [ ] Controlled failback produces one opposite-direction notification.
- [ ] Verify a loss-of-active-node scenario, or an approved equivalent fault simulation, demonstrates the surviving node can alert without the original node being reachable.
- [ ] Temporarily unavailable local exporter (test fixture or approved fault scenario) does not produce false failover alerts and Docker monitoring continues.
- [ ] Verify syslog end-to-end; test other configured alert channels as applicable.
- [ ] Observe bounded CPU/memory/HTTP impact and document expected limitations, rollback, and compatibility.

### Definition of Done

The implementation is ready for review/release when all automated tests and CI pass, controlled two-node role-change and failback tests produce the expected one-per-transition notifications, an active-node loss is covered by the surviving watchdog, resource use remains within current watchdog limits, documentation shows how to enable/disable the option on both nodes, and existing non-HA functionality is unchanged. No HA commands that modify cluster state or customer production services are introduced.

## 7. Release gates, risks, and intentional exclusions

**Before implementation/release:** confirm the standby .242 has its own exporter on localhost:9664 and watchdog running; verify both nodes can send syslog/other customer notifications. The remote connection refusal on .242 is **not proof** its local service is absent; it may be a firewall rule. No cross-node port exposure is required for the proposed design.

**Known limitations:** If the surviving node has no running watchdog/exporter or has never recorded the original active node, this check cannot reconstruct the failover after the fact. Polling can miss a transition that changes twice between samples. Metrics cannot distinguish planned switchover vs unexpected failover, and promotion does not guarantee the entire application is healthy.

**Out of scope:** HA orchestration/control, restarting or promoting resources, automatic remediation, logging every Pacemaker transition, live log parsing, new monitoring dashboards/exporters, historical HA event reconstruction, generalized multi-service health monitoring, and remediation of the native Prometheus 9002 vs 9664 mismatch. The latter should be handled as a separate CyberController native monitoring correction.

**Implementation status:** Approved by the user on 2026-10-09 and implemented on the feature branch. Do not merge or release until the two-node acceptance gates in [HA_VALIDATION.md](HA_VALIDATION.md) are satisfied.
