# v1.6.0 Release — Changes Since 1.5.4

**Status:** Release candidate, not yet tagged or published. Version remains `1.6.0` in `VERSION`. The [release PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) targets `main`. This file documents everything included in the upcoming release relative to v1.5.4, not a claim that an image or deployment has been published.

## Feature and fix inventory

| Area | Change since 1.5.4 | Change record |
|---|---|---|
| Baseline reliability and installation hardening | Serialize state persistence under concurrent event/poll writers; make manual exec probes bounded; consolidate post-reboot alert summaries; secure runtime config file as root-owned and group-readable; verify post-install health; improve image-archive validation and uninstall so unrelated tags are preserved | [PR #3](https://github.com/Radware/CyberController-Container-Alert/pull/3) |
| Docker event consistency | Resolve event identity by Docker ID; prevent historical names from corrupting a new container with the same name; rate-limit removed-container warnings; protect event processing from removed IDs | [PR #6](https://github.com/Radware/CyberController-Container-Alert/pull/6) |
| Restart-loop lifecycle | Count restarts using Docker's authoritative `RestartCount`; deduplicate CRASH / RESTART-LOOP notifications, prevent health checks from replacing active loop incidents; require sustained uptime before RECOVERED | [PR #5](https://github.com/Radware/CyberController-Container-Alert/pull/5) |
| Planned container shutdown | Correlate Docker stop intent with late / reversed `die` events, suppress false CRASH for legitimate Docker stops; distinguish SIGKILL exit 137 from confirmed kernel OOM; protect stop evidence across container starts | [PR #7](https://github.com/Radware/CyberController-Container-Alert/pull/7) |
| Optional CyberController HA change alert | Detect promoted/active HA Operator from existing Pacemaker exporter, not the Pacemaker DC. Separate HA polling from Docker; quiet baseline/rebaseline; only new active owner sends one WARNING on real failover/failback. Reuses existing notifications, state persistence and SNMP MIB | [PR #4](https://github.com/Radware/CyberController-Container-Alert/pull/4) |
| Release documentation | Clarify merged vs unreleased features and explicit image version/tag process | [PR #8](https://github.com/Radware/CyberController-Container-Alert/pull/8) |
| Notification consistency | Preserve INFO recovery and WARNING HA Syslog independently of local logging threshold; consistent Syslog priorities and alert fields, explicit HA/reboot event identifiers; retain SNMP format and five var-binds | [PR #10](https://github.com/Radware/CyberController-Container-Alert/pull/10) |
| Crash/OOM recovery correction | Apply the existing configurable `restart_recovery_stability_seconds` gate to CRASH, OOM and RESTART-LOOP recoveries, even when a policy-`no` container was started manually or by an external agent | [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) |
| Installer source fallback | Source-build mode copies both `lifecycle.py` and `ha_monitor.py` alongside `watchdog.py` and Dockerfile; normal versioned offline archive flow retained | [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) |
| Customer-facing alert descriptions | Approved factual wording for CRASH, OOM, UNHEALTHY, RESTART-LOOP, RECOVERED, REBOOT-SUMMARY and unknown fallback. Keep HA-FAILOVER advice unchanged. Email/Slack use `Context` rather than `Action`; structured Syslog `action=` remains unchanged for compatibility | [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) |
| Test isolation and reliability | Eliminate live-Docker destructive test execution. Smoke suite requires a verified, labelled, empty Engine on a GitHub-hosted disposable runner, not an arbitrary DOCKER_HOST. Validate fixture exit events, cleanup ownership and absence of leftovers, and lazily resolve Docker socket GID | [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) |

## Customer-facing message catalogue (v1.6.0)

These strings are informational, not instructions to change production configuration. The existing incident severity and detection evidence remain intact.

| Event | Severity | Context text |
|---|---|---|
| `crashed` | CRITICAL | Docker reported an unexpected container termination. Run: `docker logs <name> --tail 50`. The exit code and detection details provide additional context. |
| `oom` | CRITICAL | Docker reported that the container was terminated due to an out-of-memory condition. |
| `unhealthy` | HIGH | The container is running, but a monitoring health check is failing. |
| `restart-loop` | HIGH | The container has restarted repeatedly within the configured monitoring window. This pattern may be associated with application errors, resource pressure, or unavailable dependencies. The underlying cause has not been determined. |
| `recovered` | INFO | The previously reported alert condition has cleared according to the available monitoring checks. |
| `reboot-summary` | INFO or HIGH | Monitoring has resumed following a host reboot. The reported summary includes container status, recovered incidents, and any conditions that remain unresolved. |
| `ha-failover` | WARNING | Verify whether the HA switchover was expected and check service health. **Unchanged.** |
| Unknown type | WARNING | A container monitoring event was reported. The failure type and available detection details provide additional context. |

The CRASH read-only logs command is intended for authorized diagnostic access. Customers must not modify Docker Compose, memory limits, restart policies, or production resources based solely on an alert. Configuration remediation is determined by Radware R&D or its authorized operational process.

**Compatibility:** No change to machine event identifiers, severities, HA identities, container IDs, OIDs/var-binds, probe details, or Syslog key `action`. Only human-facing text and email/Slack label `Context` change. No new alert transport, background service or message queue.

## Verification and safety boundaries

- Automated regression tests run in CI without destructive Docker operations. The additional smoke guard tests use a fake Docker executable and must confirm unsafe endpoints are rejected without contacting Docker.
- Destructive real Docker failure testing is **only** through the manually dispatched [Release smoke (isolated)](../.github/workflows/release-smoke.yml) workflow on an ephemeral GitHub-hosted runner with an empty labelled nested Engine. See [procedure](RELEASE_SMOKE_TESTING.md).
- Historical test evidence is retained: [v1.6.0 final 2026-10-14 acceptance](RELEASE_ACCEPTANCE_V1.6.0_FINAL.md), [earlier live HA-lab findings](RELEASE_VALIDATION_V1.6.0.md), and JSON files in this directory. **That evidence predates the latest notification wording and fail-closed test-runner changes**. A fresh hosted workflow result must be associated with the current PR commit.
- During previous m920q nested-engine testing, the outer test container was visible to the host's normal watchdog and caused genuine HIGH/CRITICAL notifications. This is why the nested Engine is now allowed **only on a disposable hosted CI machine** and never on m920q or CyberController.
- Existing lab watchdog images are **not automatically upgraded** by a PR push. The final image must be built from the approved merge commit, identified by image digest, and deployed through the existing versioned release process.

## Exclusions, limitations and release gating

**Not established by testing:** live customer SMTP, Slack and SNMP delivery end-to-end; arbitrary host reboot, network partition, STONITH, or production recovery policy. SMTP was explicitly excluded during lab acceptance. HA failover/failback was tested on real lab nodes with a prior source candidate, without any further failover required for text-only changes.

The HA notification dispatch path has no persistent retry queue (intentional v1 limitation). Docker planned-stop correlation is not an availability policy; a deliberately stopped long-running service does not automatically produce a downtime alert. Unexpected external Docker restarts were seen on a lab; the initiator was not determined. These are documented operational limitations, not release regressions.

**Before shipping:** PR #9 CI green, separate hosted real-Docker smoke green at the **same candidate commit**, code review approved, merge to `main`, build/verify exact `watchdog:1.6.0`, package signed/verified artifacts per release process, validate upgrade/rollback, tag `v1.6.0` and publish notes. None of those final publish steps is automatic.
