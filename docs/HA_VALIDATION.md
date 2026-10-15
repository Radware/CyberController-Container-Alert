# Optional HA active-node alert — validation status

**Branch:** feature/optional-ha-failover-alert
**Status:** Draft PR #4; tested in the two-node lab, not approved for merge
**Updated:** 2026-10-13

## Automated tests and build

- **26 tests pass** after the PR review fixes. Added regression coverage for an HA thread running while the Docker poll is blocked and raises, and disable -> role change -> re-enable with a quiet baseline.
- The new HA thread owns its HTTP session, uses the existing stop event and global polling interval, and does not change Docker health checks or require new dependencies.
- Earlier PR #4 CI passed, including the clean Docker build. **CI must pass again on the updated commit** before merging.
- The existing state.json v1 format and Docker container alert state are retained.

## Real two-node environment — user-initiated test (2026-10-10 UTC)

- **Both nodes have HA exporters and watchdog builds running** during the user test, not just the original active node. These installations were done by the user, not by this code review.
- At about 02:53:46 UTC the user initiated a manual switchover on .42, which moved the promoted HA Operator resource to .41.
- .42 logged at **02:55:17**: "HA role changed 192.0.2.42 -> 192.0.2.41; new owner will notify."
- .41 logged at **02:55:37**: one WARNING, "CyberController HA active node changed: 192.0.2.42 -> 192.0.2.41".
- Both nodes stayed online and .41's promoted HA resources became active. The actual switch was detected correctly, without duplicate HA events observed.
- **Actual notification delivery still needs verification.** .41 had syslog configured to 127.0.0.1:514; receiving NMS confirmation was not established. .42 used SMTP, but smtp.radware.com did not resolve, so its container failure messages were not emailed.
- During the planned demotion, the HA Operator explicitly removed vDirect, TED and Assist Service on .42. The watchdog generated separate CRITICAL crash alerts for vDirect and Assist Service. Narrowly scoped suppression is **out of scope** for PR #4 and should have a separate code review.
- A **reverse failback (.41 -> .42)**, end-to-end receiver confirmation, and a safe surviving-node test after failure of the original active node have not been confirmed.

## Release gates still open

1. CI passes for the current head, with new concurrency/re-enable tests.
2. Test a controlled **failback** from .41 to .42, verifying a single reverse alert.
3. Verify the configured receiving systems, not just local watchdog logs; resolve .42 SMTP DNS or configure a working syslog route.
4. Validate a supported active-node-loss scenario without deliberately partitioning a cluster where STONITH is disabled.
5. Confirm targeted CC-version compatibility and ordinary watchdog restart/disabled option behavior.

## Accepted v1 limitations

- One-shot best-effort dispatch: transition state is persisted **before** notification. Failed SMTP/Slack/SNMP/syslog delivery does not automatically retry. A failed state write may cause a duplicate after restart. Do not add a durable delivery queue to this PR without changing the dispatch API.
- HA SNMP events use the existing Container Watchdog MIB: cwContainerName=(HA cluster), cwFailureType=ha-failover. NMS integrations should classify by failure type. A separate MIB identity is not included in v1.
- Planned HA service removals currently produce container failure alerts. Suppression is a separate, narrowly scoped enhancement.

**No HA host, Docker service, Pacemaker, firewall, or monitoring configuration changes were made during this code-review update.**
