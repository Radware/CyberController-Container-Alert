# Container Alert v1.6.0 - Final release-candidate acceptance


> **Historical acceptance evidence (October 14, 2026), not current test instructions.**
> These records predate the v1.6.0 hosted-only smoke-test safety guard and final
> customer-facing message updates. Do **not** rerun their Docker commands on
> any shared or operational Docker Engine. Earlier nested testing triggered
> real notifications on its parent host.
> See [current release notes](RELEASE_NOTES_V1.6.0.md) and
> [safe smoke workflow](RELEASE_SMOKE_TESTING.md).

**Validation date:** October 14, 2026 (UTC)
**Release PR:** [#9](https://github.com/Radware/CyberController-Container-Alert/pull/9)
**Source basis:** release branch including merged [PR #10](https://github.com/Radware/CyberController-Container-Alert/pull/10), plus the targeted recovery fix in this PR
**Verdict: PASS for the tested Docker, HA, Syslog, regression, and image-packaging acceptance criteria.** Live SMTP, SNMP, Slack, destructive HA injection, and host reboot are not included.

## Executive results

| Area | Result | Evidence |
|---|---|---|
| Automated Python regression tests | **PASS 109/109** | `python3 -m unittest discover -s tests -q` inside actual rebuilt `watchdog:1.6.0-rc-fix` image |
| Full real Docker integration suite | **PASS 15/15** | Isolated Docker Engine on a dedicated development host, run `261014224557488`; 22:45:57-22:52:41 UTC; 404.4 seconds |
| Real external restart after OOM | **PASS 1/1** | Separate targeted real OOM run `261014224323244`; RECOVERED after 120.6s |
| Regression reproduction before fix | **CONFIRMED FAIL** | Real crash followed by manual Docker start produced premature RECOVERED within about three seconds; run `261014223252035` |
| Raw UDP Syslog integration | **PASS 15/15** | 15 locally generated `watchdog_alert` records delivered as 15 actual UDP datagrams to isolated collector |
| Two-node HA failover and failback | **PASS** | Previous *live* lab testing: promoted HAOperator `.41 -> .42 -> .41`; one new-owner WARNING per transition, no duplicate, both DFC Syslog receivers confirmed |
| Installer source-build / offline image bundle | **PASS** | Source-build includes `watchdog.py`, `lifecycle.py`, `ha_monitor.py`; image save/load preserved ID |
| Disposable test cleanup | **PASS** | No labelled Docker test containers or volumes remained after each completed suite |
| Parent-host watchdog impact | **NONE** | Existing `watchdog:1.5.4` remained healthy, zero restarts, and saw no isolated Engine fixtures |

## Reproducible environment

- On a **dedicated development host**, ran a short-lived Docker-in-Docker engine `docker:27.5-dind`, exposed only at `127.0.0.1` using an ephemeral TCP port; it had a distinct Engine ID from the parent host's Docker Engine, memory limit 2 GiB, CPU limit 2 cores, no bridge/network provisioning inside the test engine, and its state was disposable.
- Real test observer ran the revised application code from `watchdog:1.6.0-rc-fix` with its own state volume and a 2s polling interval. Original 5-restart threshold and **120s recovery stability window** were preserved. The observer monitored **real Docker Engine events and cgroup OOMs**, not synthetic test events.
- Disposable failure containers were scoped with unique `ca-smoke-...` names/labels, limited CPU/memory, and no network access. Syslog receiver was a temporary UDP/514 collector on the isolated Engine. No production SMTP or external Syslog destinations were used.
- The built test image contained verified source hashes:
  - `watchdog.py`: `4df8451b496a2935eba17fd56cc87459c87b4f96d9c2ace5bee9161fdf2c60fa`
  - `lifecycle.py`: `55b6a06ebb69cb5a70f6c1566798087510228ef93eeceb810dcadb5da09d81f9`
  - `ha_monitor.py`: `0fb3ba165035c9970f91854914709fd7492e21a7583c74f34174275c12083dbd`
- Test image ID within the isolated Engine: `sha256:670cbdd3a24317aa39c04036d1c90c72420c97a0afe32ad5e4a77e06ef464be9`. This was a locally built test candidate, **not** a published release artifact or Git tag.
- Real-event run captured **92 Docker events**, including 18 `die`, 22 `start`, 1 `oom`, 1 `stop`, native unhealthy/healthy transitions, and **zero unexpected `restart` actions**. The regular loop's expected internal restart behavior is tracked via Docker's actual `RestartCount`.

## Full real Docker acceptance - final clean run

| Test | Expected | Observed | Status |
|---|---|---|---|
| T01 CLEAN_EXIT | Exit 0, no incident | No alert | **PASS** |
| T02 UNEXPECTED_CRASH | Exit 2, one CRITICAL crash | Exactly one CRITICAL CRASH | **PASS** |
| T03 PLANNED_STOP_137 | Forced Docker stop/137 not mistaken for crash or OOM | No false alert | **PASS** |
| T04 DIRECT_SIGKILL | Direct signal 9/137 is crash, not OOM | `OOMKilled=false`, one CRITICAL CRASH | **PASS** |
| T05 REAL_OOM | Real kernel OOM, 137, single OOM alert, no redundant CRASH | `OOMKilled=true`, one CRITICAL OOM | **PASS** |
| T06 RESTART_LOOP | Repeated failures escalate after at least five real restarts | `RestartCount=5`, one CRASH, one HIGH RESTART-LOOP | **PASS** |
| T07 BRIEF_RECOVERY | 11 seconds of RUNNING is insufficient | Zero RECOVERED after 11 seconds | **PASS** |
| T08 INTERRUPTED_RECOVERY | A new actual failure restarts the stability clock | `RestartCount` 6 to 7; no early RECOVERED | **PASS** |
| T09 STABLE_RECOVERY | One INFO RECOVERED after 120s sustained uptime | One CRASH, one LOOP, one RECOVERED | **PASS** |
| T06-09 LIFECYCLE | One complete incident, no duplicate notifications | Correct alert order and totals | **PASS** |
| T14 EXTERNAL_RESTART | Manual start of policy-`no` crashed container must wait 120s | One RECOVERED near 120s (119.9s by second-resolution alert timestamp) | **PASS** |
| T10 NAME_REUSE | New Docker ID with reused name must start a new incident | Two distinct IDs, one CRASH per ID | **PASS** |
| T11 FAST_REMOVAL | Rapid `--rm` events do not flood missing-ID warnings or kill listener | Three ephemeral containers, zero observed warnings, observer alive | **PASS** |
| T12 NATIVE_HEALTHCHECK | Native Docker unhealthy then healthy | One HIGH UNHEALTHY, one INFO RECOVERED | **PASS** |
| T13 SYSLOG | Every generated alert reaches receiver with correct typed severity, no planned-stop false alarm | **15/15** structured UDP datagrams; no planned-stop false alarm | **PASS** |

The isolated collector captured actual raw Syslog priorities: **CRITICAL `<130>` (10), HIGH `<131>` (2), INFO `<134>` (3)** with local0 facility. This verifies PR #10's intended priority mapping for Docker event classes. Previous real HA testing captured and stored HA WARNING through DFC (local0.WARNING `<132>`).

### Additional OOM-then-manual-start case

The test `T15_EXTERNAL_OOM` deliberately exceeded a 64 MiB cgroup memory limit, confirmed exit 137 with Docker `OOMKilled=true`, received one CRITICAL OOM alert, then manually started the same policy-`no` container with a healthy process. No early recovery was emitted; precisely one INFO RECOVERED occurred **120.6 seconds** after actual Docker uptime began. **PASS.**

## Defect, fix, and historical HA-lab failures

The earlier HA-lab smoke run (`261014214436754`, originally **11 PASS / 3 FAIL**) observed containers with Docker restart policy `no` starting again after a failure despite not being controlled by the test suite. The captured events prove this external restart behavior; the initiator was **not positively identified**. Do not attribute it to a particular CyberController host service without direct evidence.

We reproduced the **application bug independently** on the isolated test Docker Engine: the old code emitted RECOVERED just a few seconds after a manual `docker start` of a crashed policy-`no` container. Original `_maybe_alert_recovery` applied the 120-second stability gate to CRASH/OOM only when the Docker restart policy was automatic. That assumption is invalid when an external manager manually restarts failed containers.

**Minimal code correction:** for any open `crashed`, `oom`, or `restart-loop` incident, require sustained Docker uptime and no recent failure for the existing recovery-stability interval. The existing state machine, Docker event correlation, HA monitoring, and notification APIs were not redesigned. Two policy-`no` crash/OOM regressions now protect this path. All **109** automated tests pass.

The original lab's external Docker restarter remains **unattributed and unchanged**. Its behavior is an integration-environment consideration, not proof the new application fix failed. The isolated Engine recorded zero unrelated restart actions and its full suite passed.

## HA acceptance carried forward (actual Pacemaker operations)

The user manually performed real switchover and failback of the existing two-node CyberController HA cluster while both real watchdogs ran the PR #10 1.6.0 candidate.

| HA case | Evidence and outcome |
|---|---|
| `192.0.2.41 -> 192.0.2.42` | **PASS**: promoted operator moved to .42; one WARNING alert on newly active .42 at 20:56:16 UTC, no duplicate on .41 |
| .42 DFC delivery | **PASS**: structured `ha-failover` record received/stored in `config_dfc_1:/var/log/syslog` |
| `192.0.2.42 -> 192.0.2.41` | **PASS**: one WARNING alert on .41 at 21:09:47 UTC; .42 deferred, no duplicate |
| .41 DFC delivery | **PASS**: structured reverse alert received/stored in its DFC Syslog receiver |
| Final HA health | **PASS**: quorum healthy, .41 promoted, both watchdogs healthy and baselines .41 |

The new Docker recovery fix does not change the HA monitoring code. Nevertheless, **the currently running HA-node images still contain the previous pre-fix candidate**, so they are not proof that the final recovery-fixed image has been installed on those nodes.

## Packaging, exclusions, and release decision

Installer source-build and offline archive `docker save`/`docker load` procedures were validated earlier in this release cycle; CI covered shell syntax, Compose, code compilation, unit tests, and image builds. The test suite itself runs without installing packages on operational HA nodes.

**Not validated end to end:** SNMP traps to a physical NMS, Slack delivery, SMTP delivery (explicitly excluded by user), host reboot, network partition, STONITH, destructive exporter injection. These are not represented as passed. Failover/failback, DFC Syslog, ordinary Docker incidents, and the fixed recovery path have direct live evidence.

**Release recommendation:** The previously blocking Docker recovery bug is fixed and independently validated. The release branch is ready for final PR review and fresh CI validation. Do **not** claim that the Git tag, GitHub Release, or final v1.6.0 package was published; do not deploy or merge without the user's chosen release step.

## Evidence and replay

- [Final full suite JSON](RELEASE_SMOKE_V1.6.0_ISOLATED_20261014.json) - 15/15 and cleanup evidence
- [Before-fix real failure reproduction](RELEASE_SMOKE_V1.6.0_PRE_FIX_REPRO_20261014.json)
- [Real OOM/manual-restart 120.6s proof](RELEASE_SMOKE_V1.6.0_OOM_RESTART_20261014.json)
- [Earlier HA-lab conditional report](RELEASE_VALIDATION_V1.6.0.md)
- [Reusable real Docker smoke runner](../tests/release-smoke.sh)
- Historical runtime evidence: temporary smoke-run directories containing test events, Syslog records and observer state were retained during validation.
