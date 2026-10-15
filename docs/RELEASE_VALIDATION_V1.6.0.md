# Container Alert v1.6.0 — Release Acceptance Validation


> **Historical acceptance evidence (October 14, 2026), not current test instructions.**
> These records predate the v1.6.0 hosted-only smoke-test safety guard and final
> customer-facing message updates. Do **not** rerun their Docker commands on
> any shared or operational Docker Engine. Earlier nested testing triggered
> real notifications on its parent host.
> See [current release notes](RELEASE_NOTES_V1.6.0.md) and
> [safe smoke workflow](RELEASE_SMOKE_TESTING.md).

> **Historical baseline (pre-fix).** This page records the original HA-lab smoke-test failures. They were subsequently investigated using a separate real Docker Engine on a dedicated development host, and a minimal recovery fix passed the complete [final release acceptance report](RELEASE_ACCEPTANCE_V1.6.0_FINAL.md). Retain this document as evidence of the original findings, not the current release verdict.

**Date:** October 14, 2026 (UTC)
**Repository:** `Radware/CyberController-Container-Alert`
**Release PR:** [#9](https://github.com/Radware/CyberController-Container-Alert/pull/9) — open
**Notification fix:** [#10](https://github.com/Radware/CyberController-Container-Alert/pull/10) — merged into release branch
**Source revision:** `3b1de153e4d6c1b5ef33ca90c0b6ef755727fb05`
**Lab image:** `watchdog:1.6.0`, image ID `sha256:e3d02f6ef56cde0bc7021919def9e240505434a15769aac3884cf15ac8f0ccff`
**Verdict: CONDITIONAL / NOT FULLY ACCEPTED.** HA passes. Most real Docker cases pass; some checks are confounded by out-of-band Docker restarts and expose premature recovery / extra alert behavior. Do not represent the entire release as green.

## Test methods and safeguards

- Tested against a real Docker Engine in the HA test environment (`192.0.2.41`). No simulated Docker events.
- A separate resource-limited v1.6.0 observer used its own Docker volume/state, 2-second Docker polling, real 5-restart threshold, and **real 120-second** recovery requirement. SMTP disabled only on the observer; Syslog sent to the **existing** DFC UDP/514 receiver. Production-style main watchdog and cluster were not reconfigured.
- Used uniquely prefixed, labeled disposable containers, restricted to 0.2 CPU and 64–128 MiB each, with `--network none`. Verified actual Docker state, events, watchdog structured alert records, and DFC `/var/log/syslog` deliveries.
- No host packages, new Docker networks, Pacemaker operations, or host service modifications. Cleanup removed all smoke-owned containers and volumes. Original watchdog stayed healthy.
- Runner: [`tests/release-smoke.sh`](../tests/release-smoke.sh). It saves `report.json`, `report.md`, `watchdog.log`, `docker-events-live.jsonl`, and `dfc-syslog.txt` to `/tmp/container-alert-smoke-<run-id>/` inside the authorized RDC client.

## 1. Automated, packaging, and release checks

| Check | Acceptance | Result |
|---|---|---|
| Automated regression suite | All tests pass against final built source | **PASS — 108/108** inside a test Docker image |
| GitHub CI for PR #9 head | Green | **PASS** on `3b1de153` (before adding this documentation) |
| Release code/image integrity | Embedded `watchdog.py`, `lifecycle.py`, `ha_monitor.py` hashes match Git | **PASS** |
| `install.sh` / smoke script syntax | `bash -n`, smoke embedded Python compiles | **PASS** |
| Docker Compose build configuration | Valid config with `WATCHDOG_VERSION=1.6.0` | **PASS** |
| Installer source-build fallback | Dockerfile and three Python modules build/import | **PASS** with normal cached/online Docker build |
| Offline archive | `docker save` / SHA-256 / `docker load`; same release image ID | **PASS** |
| Source-build without network | All `pip` dependencies available offline | **NOT SUPPORTED**. No-network source build failed because pip must download dependencies; use the tested `watchdog.tar` for offline installations |

## 2. Real two-node HA acceptance (completed before Docker smoke)

| Scenario | Acceptance | Actual result |
|---|---|---|
| Baseline | Both nodes agree `.41` active, no startup failover | **PASS** |
| Failover `.41 → .42` | One WARNING on new owner, zero on previous | **PASS** — alert on `.42` at **20:56:16 UTC**; `.41` logged "new owner will notify" |
| HA Syslog on `.42` | Structured failover record delivered to DFC | **PASS** — stored in DFC `/var/log/syslog` |
| Failback `.42 → .41` | One WARNING on new owner, zero on previous | **PASS** — alert on `.41` at **21:09:47 UTC**; `.42` logged owner defer |
| HA Syslog on `.41` | Structured failback record delivered to DFC | **PASS** |
| Final cluster status | One promoted HA Operator, quorum, both watchdogs healthy | **PASS** — `.41` active; both watchdogs image `1.6.0`, healthy, zero restarts |
| Transient exporter ambiguity during transition | No fabricated failover | **PASS** — `.42` logged one invalid-promoted-owner observation at 20:54:16 UTC, then recovered and sent correct alert |
| SMTP delivery | Excluded by test owner | **EXCLUDED** — SMTP network timeouts are not part of this verdict |

## 3. Real Docker acceptance — final run

**Final run ID:** `261014214436754`
**Time:** 21:44:36–21:48:30 UTC (233.9 seconds)
**Final summary: 11 PASS / 3 FAIL** across 14 runner assertions (includes a grouped restart lifecycle check).

| ID | Scenario / acceptance criteria | Result and evidence |
|---|---|---|
| T01 | Exit 0 yields no crash alert | **PASS** |
| T02 | Nonzero exit 2 yields exactly one CRITICAL crash | **PASS** |
| T03 | Planned `docker stop`, SIGKILL/137, yields no downstream false crash | **FAIL (interference)**. Initial Docker stop had matching `stop/die` evidence; the lab later restarted the stopped fixture, and the subsequent forced cleanup generated an additional CRASH |
| T04 | Direct SIGKILL/137 has `OOMKilled=false`, one CRITICAL crash, no false OOM | **PASS** in final run |
| T05 | Genuine cgroup OOM; Docker confirms `OOMKilled=true`; one CRITICAL OOM, no duplicate | **FAIL (unstable state)** in final run. Earlier run confirmed true `OOMKilled`; final live event capture proved **12 real OOM events** after unexpected restarts, so the subsequent Docker state was no longer the first killed instance |
| T06 | Restart loop escalates after 5 actual restarts, with one CRASH and one HIGH RESTART-LOOP | **PASS** — Docker `RestartCount=6` |
| T07 | Short period running does not prematurely recover restart loop | **PASS** — 11 seconds healthy, zero RECOVERED |
| T08 | New actual failure during recovery interrupts stability clock | **PASS** — Docker restart count incremented 6→7; no early RECOVERED |
| T09 | After at least 120 seconds sustained recovery, exactly one INFO RECOVERED | **PASS** — one CRASH, one RESTART-LOOP, one RECOVERED |
| T10 | Reuse exact container name with a different ID gets a fresh incident | **PASS** — 2 unique IDs, one crash per instance |
| T11 | Three `--rm` containers do not flood missing-container warnings or kill event listener | **PASS for no-flood** — 0 warnings observed; missing-ID race branch not independently forced |
| T12 | Native Docker health check transitions unhealthy→healthy | **PASS** — one HIGH UNHEALTHY, one INFO RECOVERED |
| T13 | All correct structured alert records arrive at DFC; no false planned-stop alert | **FAIL strict semantic criterion** — **26/26** actual watchdog records reached DFC, but one unwanted planned-fixture CRASH also reached it |
| Grouped T06–T09 | Correct restart-loop lifecycle as a whole | **PASS** |

### Three attempts and reproducibility

- **First run** (`261014212952944`): core real-event cases, name reuse, health, and DFC transport passed. A **test-runner formatting error** (missing grouped-test return value) prevented a clean report; fixed afterward. Unexpected out-of-band restarts began causing extra OOM/recovery alerts.
- **Second run** (`261014213819904`): **12 PASS / 2 FAIL**. Unexpected SIGKILL fixture restart and false recovery of an OOM fixture were observed. The runner was corrected to record these explicitly.
- **Final run** (above): **11 PASS / 3 FAIL**; includes live capture of **206 Docker events**. The failure conditions vary with the lab's externally initiated restarts, not a deterministic source-code change.

### Investigation: out-of-band restarts

The live event capture on `.41` included **37 `restart` actions, 57 starts, 54 dies, and 12 `oom` actions** for test-tagged containers. Importantly, some affected fixtures had `HostConfig.RestartPolicy.Name=no`, yet their Docker IDs were started/restarted repeatedly. The initiator of these restarts was **not established**; do not attribute them to Container Alert without additional evidence.

The planned-stop fixture initially emitted real `kill(SIGTERM)`, `kill(SIGKILL)`, `stop`, and `die(137)` events. The unwanted CRASH appeared **later**, when that fixture had already been restarted and the test suite used force-remove to clean it up; that final kill had no `stop` evidence. This is not evidence that the original `docker stop` was misclassified.

The OOM fixture produced genuine kernel `oom` and `die(137)` events. When another actor immediately restarts a container whose Docker policy is `no`, the current recovery rule can declare an OOM/CRASH incident recovered after only a brief running interval. Repeated OOM/recovery alert pairs were observed in the earlier run. **This is a real edge case worth addressing or accepting explicitly**, separate from the one-shot OOM detector.

The DFC Syslog pipeline itself had **100% message-count correspondence in the final run (26/26)** and preserved structured severity/type fields. However, transport delivery is not the same as correct incident classification.

## 4. Additional coverage and explicit exclusions

- PR #10 automated tests check alert severity mapping, SMTP/Slack formatting, missing container fields on HA alerts, and mocked SNMP alert dispatch. The existing five-var-bind SNMP MIB mapping was kept unchanged, but it was **not verified by a live SNMP trap receiver**. Lab smoke verified real Syslog emission/receipt of CRITICAL, HIGH, and INFO severity fields; it did **not** independently capture raw UDP PRI for every severity.
- Live SNMP trap delivery, Slack delivery, and SMTP delivery were **not** exercised end-to-end; these require configured destinations (SMTP explicitly excluded by the owner).
- Real network partition, forced host reboot, Pacemaker failure injection, and blocked Docker daemon were intentionally **not** performed. HA independence and exporter failure handling are covered by automated tests; the transient exporter error was also observed during actual switchover.

## 5. Cleanup, impact, and release decision

- Both HA nodes remain on `watchdog:1.6.0` with their existing configs/state and healthy watchdogs; cluster role and quorum remain intact.
- All test-labelled temporary containers and named volumes were removed. No host packages, new networks, firewall rules, or HA resources were installed/changed. Report/evidence files reside in the RDC admin client's `/tmp` (ephemeral, not a host install).
- **Do not mark complete release acceptance yet.** HA, regression suite, packaging, and Syslog transport are verified. Real Docker repeated OOM/recovery and forced-cleanup false-alert behavior need a controlled-engine rerun or an explicit scoped acceptance.
- Minimal next investigation: isolate the lab's external Docker restarter, reproduce crash/OOM with exactly one failure, and decide whether all crash/OOM recoveries should require the already-existing 120-second continuous-uptime check (not just containers with Docker auto-restart). Avoid broad lifecycle redesign before validating that decision.
- PR #9 remains **unmerged**; do not tag or publish `v1.6.0` solely on these conditional results.

## Test evidence pointers

- Lab RDC container: `/tmp/container-alert-smoke-261014214436754/`
- Raw files: `report.json`, `report.md`, `docker-events-live.jsonl`, `watchdog.log`, `dfc-syslog.txt`, and `observer-state.json`.
- Source-controlled runner: [`tests/release-smoke.sh`](../tests/release-smoke.sh). Use `bash release-smoke.sh` from the authorized lab admin environment; its `/tmp` filesystem is mounted `noexec`.

Machine-readable final run: [`RELEASE_SMOKE_V1.6.0_20261014.json`](RELEASE_SMOKE_V1.6.0_20261014.json).
