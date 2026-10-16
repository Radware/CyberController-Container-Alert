# CyberController Container Watchdog

The **CyberController Container Watchdog** is a lightweight, autonomous monitoring service that continuously tracks the health of all Docker containers on the host. It detects failures — including crashes, out-of-memory kills, prolonged unhealthy states, and restart loops — and dispatches real-time alerts through one or more configurable channels (**Slack**, **SMTP**, **SNMP Traps**, or **Syslog**). It also sends a one-time INFO **recovered** alert through the same channels once a previously-alarmed container returns to normal operation.

---

## Table of Contents

1. [How It Works](#1-how-it-works)
2. [System Overview](#2-system-overview)
   - [Project Structure](#project-structure)
   - [Image Size](#image-size)
   - [Python Dependencies](#python-dependencies)
3. [Configuration](#3-configuration)
4. [Supported Alert Channels](#4-supported-alert-channels)
5. [Deployment Instructions](#5-deployment-instructions)
6. [Failure Types & Severities](#6-failure-types--severities)
7. [Verify Watchdog Operation](#7-verify-watchdog-operation)
8. [Troubleshooting](#8-troubleshooting)
   - [Service Fails to Start](#service-fails-to-start)
   - [Alert Notifications Not Received](#alert-notifications-not-received)
   - [Duplicate and Excessive Alert Notifications](#duplicate-and-excessive-alert-notifications)
   - [Probe Type Not as Expected](#probe-type-not-as-expected)
   - [Viewing Logs](#viewing-logs)
9. [Container Probe Map (This Deployment)](#9-container-probe-map-this-deployment)
10. [Version History](#10-version-history)

---

## 1. How It Works

The watchdog runs two parallel monitoring paths:

| Path | Mechanism | What It Catches |
|------|-----------|-----------------|
| **Event stream** | `docker events` (real-time) | Crashes (`die`), OOM-kills (`oom`), health status changes |
| **Active Probes** | `docker ps` + Probes | **Integrity**: Periodic verification of liveness, stuck states, and restart-loops |

The watchdog selects one of **4 active probe types** per container, chosen automatically based on the container's configuration. Probe selection is cached for the container's lifetime and re-discovered on restart.

1. **Docker HEALTHCHECK** *(passive)* — for containers that have a `Docker HEALTHCHECK` directive in their Dockerfile. The watchdog reads the result from `container.attrs["State"]["Health"]` rather than running its own probe. When Docker reports `health_status: unhealthy` 3 consecutive times, an alert fires. The last health check output is included in the alert's Detection field.

2. **HTTP GET** *(active)* — for containers with **no** `Docker HEALTHCHECK` that expose HTTP ports. Auto-discovers a working endpoint by trying common paths (`/-/healthy`, `/health`, `/healthz`, `/metrics`, `/`) on each exposed internal port, and across all internal IPs when the container is attached to multiple Docker networks (configurable via `auto_health_check.paths` in `watchdog-config.yaml`). Returns healthy if `status_code < 500`. The discovered URL is cached for the container's lifetime.

3. **TCP connect** *(active)* — if no HTTP path responds on a port, falls back to a raw `socket.create_connection()` on that port. A successful connect confirms the service is listening; a refused or timed-out connection triggers an `unhealthy` alert. Used by databases, message brokers, and any non-HTTP service.

4. **`/proc` alive check** *(active)* — last resort when the container exposes no ports at all, or all TCP connects fail during initial discovery. Reads `/proc/1/status` inside the container via `exec_run` and checks that PID 1 is in a live state (R/S/D). A zombie (Z) or stopped (T) PID 1 triggers an `unhealthy` alert.

Alerts are deduplicated via a **cooldown** — once an alert fires for a container, the same failure type is suppressed for `cooldown_minutes` minutes. The cooldown resets automatically when the container recovers.

Two additional deduplication rules suppress redundant alerts at the event level:
- **OOM → die suppression**: when a container is OOM-killed, Docker emits both an `oom` event and an immediately following `die` event. The watchdog fires the `oom` CRITICAL alert and suppresses the subsequent `crashed` alert to avoid a duplicate for the same incident.
- **Restart-loop incident coalescing**: Docker `die`/OOM events initiate the first failure alert (nonzero `die` events may be held for the short stop-correlation window). Only periodic Docker `RestartCount` increments determine the number of automatic restarts and whether the `restart_threshold` has been reached within `restart_window_minutes`; event deliveries are **never** added to the restart total. One HIGH `restart-loop` alert then suppresses per-cycle crashes, OOMs and failed Docker, manual or auto-discovered probes until sustained recovery, regardless of cooldown. Docker IDs identify distinct container instances even when names are reused.

**Docker-requested stops and exit 137:** An exit status of 137 means SIGKILL; it does **not** prove an OOM. Docker may deliver a stop event shortly after die, so nonzero exits are briefly held for lifecycle_detection.correlation_window_seconds (default 3 seconds). A matching stop from the same container ID suppresses that instance's misleading CRASH and is logged instead; a direct docker kill, an unconfirmed stop, or a genuine OOM still produces the normal CRITICAL incident. A start event prevents the following run's stop from masking an earlier crash. This correlation only provides evidence of a **Docker-requested stop**, not evidence that stopping the service was authorized. The existing restart-loop counting, recovery rules, and alert delivery remain authoritative. Set lifecycle_detection.enabled to false to disable the correlation.

Planned-stop correlation does not implement an availability policy: long-running services stopped intentionally will not produce a downtime alert unless another monitoring rule detects their outage. Optional expected-running monitoring and HA-specific planned-shutdown handling are tracked separately.

**Recovery alerts**: once a previously-alarmed container is healthy and running, the watchdog sends a one-time INFO `recovered` notification (if `alert_on_recovery: true`). For an auto-restarting container recovering from `crashed`, `oom`, or `restart-loop`, a fleeting `running` state is *not* recovery: Docker's `State.StartedAt` must prove at least `restart_recovery_stability_seconds` continuous uptime (default 120 seconds), with no intervening failure. On recovery, restart history is reset to avoid immediate re-alerting. Docker health events cannot bypass this check. Other container recovery checks preserve their existing behavior.

---

### Optional HA active-node change alert

On CyberController HA installations, the watchdog can optionally alert when the
**promoted HA Operator resource** moves from one cluster node to another.
This is an additional application event check; it does not replace or modify
existing Docker container health checks.

Enable the following block on **both** HA nodes, after verifying that each
node has a local Pacemaker HA cluster exporter:

```yaml
ha_monitor:
  enabled: true
  exporter_url: http://127.0.0.1:9664/metrics
```

The watchdog reads HA cluster resource metrics and validates local-node identity,
cluster quorum, and Pacemaker scrape success. The first valid reading
establishes a baseline without alerting. A new active node must be confirmed
on two consecutive successful polls (usually 60 seconds apart). Only the
newly active node sends one **WARNING: CyberController HA active node changed**,
using the already configured Syslog, SMTP, Slack, and/or SNMP channels.
Failback is handled the same way; HA state persists in the normal state.json.
HA polling runs in its own lightweight thread with a separate HTTP session, so
slow or unavailable Docker API probes do not block HA detection.

An ordinary watchdog restart with HA enabled retains its baseline. If HA
monitoring is **disabled and the watchdog restarted**, the old HA baseline is
cleared; re-enabling and restarting establishes a new baseline silently rather
than reporting an unobserved, potentially old role change.

**Delivery is best-effort:** after confirming a change, the watchdog saves its
new baseline before dispatching. If SMTP/Slack/SNMP/syslog delivery fails,
this event is logged but is **not retried** by the HA monitor. Validate the
notification path on both HA nodes. A failed state-file save may result in a
duplicate alert after a restart.

**SNMP compatibility:** HA events use the existing Container Watchdog MIB with
cwFailureType=`ha-failover`, cwContainerName=`(HA cluster)`, and old/new
node values in cwSummary/cwProbeDetail. NMS integrations should distinguish
HA events by cwFailureType, not by the container-name field.

This check is **off by default**. It does not install an HA exporter, open a
firewall port, execute Pacemaker commands, or modify cluster roles. Both
watchdogs/exporters must be running and able to send notifications for detection
of a failure of the original active node. If a node is newly installed after
the failover, the first reading is a quiet baseline and **cannot reconstruct
the earlier event**. Loss of quorum or exporter connectivity is logged locally,
not misreported as a completed failover. Observed time may be later than the
actual Pacemaker transition. A confirmed promotion does not guarantee all
CyberController services have recovered.

The exporter may use port **9664** even when the built-in CyberController
Prometheus configuration references **9002**. This optional check reads the
exporter directly and makes **no changes** to native Prometheus configuration.
See [design, test plan and release gates](docs/HA_ACTIVE_NODE_ALERT_DESIGN.md).

---

## 2. System Overview

### Project Structure

```
.
├── watchdog.py                  # Main monitor script
├── lifecycle.py                 # Bounded Docker stop/die evidence correlation
├── watchdog-config.yaml         # All configuration (no secrets)
├── docker-compose.yaml          # Production / offline runtime (image only)
├── docker-compose.build.yaml    # Developer-only source build
├── Dockerfile                   # Image build definition (python:3.11-slim)
├── requirements-watchdog.txt    # Python dependencies (reference; Dockerfile pip-installs inline)
├── install.sh                   # Guided installation from complete release package
├── uninstall.sh                 # Stop + remove script
├── scripts/package_release.sh   # CI release-package assembly (maintainers only)
├── docs/DEVELOPMENT.md         # Version-independent source-to-release workflow
├── docs/RELEASE_V1.6.0_CHECKLIST.md # v1.6.0-specific release gates
├── VERSION                       # Release version — drives the image tag and installer
├── watchdog.tar                 # Included in published release package; not tracked by Git
└── .env                          # Secrets — NOT committed to git
```

---

### Image Size

| Item | Size |
|------|------|
| Docker image (`watchdog:<VERSION>`) | ~180 MB (python:3.11-slim base + dependencies) |
| Running container (memory) | ~50–80 MB baseline; capped at `mem_limit: 256m` in `docker-compose.yaml` |
| `watchdog.tar` export | ~170 MB |

> **Note:** Image size and memory usage vary with the installed release. If resource usage is abnormal, capture Docker health, logs, and usage observations for engineering review before making production resource-limit changes.

### Container Hardening

By default the container runs as **root** so `docker compose up` works with zero configuration — no host-specific group setup needed to read `/var/run/docker.sock`. [install.sh](install.sh) instead auto-detects the socket's GID and opts into running the container as **non-root UID/GID 1000** (joined to that group via `group_add`), with no manual input required; manual installs can do the same by setting `WATCHDOG_UID`/`WATCHDOG_GID`/`DOCKER_GID` in `.env` (see [Advanced Deployment](docs/DEPLOYMENT_ADVANCED.md#configuration-files)). Either way, the container is constrained by a read-only root filesystem, no extra Linux capabilities (`cap_drop: ALL`), `no-new-privileges`, and CPU/memory/PID caps (`cpus: "0.50"`, `pids_limit: 200`, `mem_limit: 256m`), which contain a leak or runaway condition to this container instead of the host. Note that access to `/var/run/docker.sock` is effectively host-root-equivalent for **any** user (root or not): anything that can reach the socket can create privileged containers or bind-mount the host filesystem, so a compromise of the watchdog process is **not** contained by these controls — treat `docker.sock` access as the primary trust boundary when deciding who/what can reach this host.


### Python Dependencies

```text
docker==7.1.0
requests==2.32.3
PyYAML==6.0.2
pysnmp>=6.2
```


> pysnmp 6.2+ uses the modern `pysnmp.hlapi.v3arch.asyncio` API. pysnmp 4.x is no longer supported — its synchronous generator API was removed in 5.x. `pyasn1` is a transitive dependency managed by pysnmp itself and no longer needs to be pinned separately.

**Runtime (in container):** Python 3.11+

---

## 3. Configuration

The guided installer creates `watchdog-config.yaml` (monitoring policy, probes, logging and notification endpoints) and `.env` (credentials and runtime identity). The configuration is preserved on an upgrade unless the operator explicitly chooses to reconfigure it.

**For the complete list of settings, defaults, YAML examples, and instructions for applying changes after installation, see [Deployment — Configuration Reference](DEPLOYMENT.md#4-configuration-reference).** The annotated [YAML template](watchdog-config.yaml.example) and [environment template](.env.example) are also available.

A YAML-only change takes effect after restarting the watchdog. A changed `.env` value requires recreating **only** the watchdog container. Neither action rebuilds Python code into the image. Thresholds, exclusions, custom exec probes, and monitoring suppressions require authorized review; do not use them merely to silence alerts.

---

## 4. Supported Alert Channels

| Channel | YAML identifier | Credentials or other setup |
|---|---|---|
| Syslog | `syslog` | Syslog destination, port, protocol and facility |
| SMTP (email) | `smtp` | Mail server, sender, recipients, TLS/auth; credentials in `.env` if applicable |
| SNMP Trap | `snmp_trap` | NMS destination, SNMP version; SNMPv3 keys in `.env` |
| Slack | `slack` | Webhook URL stored in `.env` |

At least one channel must be selected in the installer; multiple channels may be enabled together. **See [Deployment — Notification Channels](DEPLOYMENT.md#43-notification-channels) for all configuration fields and examples.** The [advanced reference](docs/DEPLOYMENT_ADVANCED.md#snmp-trap-receiver-reference) retains SNMPv3 receiver setup and the Radware trap OID/var-bind definitions.

---

## 5. Deployment Instructions

Install **one complete versioned package** from an approved [GitHub Release](https://github.com/Radware/CyberController-Container-Alert/releases), or obtain the same package through authorized offline transfer. No Git clone, separate Docker image download, or source build is required on the customer host.

Download/transfer both `container-alert-v1.6.0.tar.gz` and `SHA256SUMS` (substitute your approved version). From the directory containing those two files:

```bash
sha256sum --check SHA256SUMS
tar -xzf container-alert-v1.6.0.tar.gz
cd CyberController-Container-Alert
bash install.sh
```

The wizard verifies and loads the included Docker image, asks for the selected notification channels, and deploys **only** the watchdog. **The v1.6.0 release archive becomes downloadable after the release is published.**

Read the full [Deployment Guide](DEPLOYMENT.md) for installation, configuration and maintenance. Developers and release maintainers: use the version-independent [Development Guide](docs/DEVELOPMENT.md) and the [v1.6.0 finalization checklist](docs/RELEASE_V1.6.0_CHECKLIST.md).

---

## 6. Failure Types & Severities

| Failure | Severity | Trigger |
|---------|----------|---------|
| `crashed` | CRITICAL | Container exited with non-zero exit code |
| `oom` | CRITICAL | Container was OOM-killed by the kernel |
| `unhealthy` | HIGH | Health probe failing for N consecutive cycles |
| `restart-loop` | HIGH | Container restarted ≥ threshold times within window |
| `recovered` | INFO | Previously-alarmed container (`crashed`/`oom`/`unhealthy`/`restart-loop`) is healthy/running again. Fires once per incident; controlled by `alert_on_recovery` (default `true`) |
| `reboot-summary` | INFO / HIGH | Sent once after a host reboot is detected, when the `boot_grace_seconds` window closes. Reports boot time, how many containers are running, which recovered, and which are still failing. Always sent after a reboot — an INFO summary confirms the node came back cleanly. HIGH if any container is still failing |

OOM alerts include **memory stats** (usage / limit / peak) prepended to the log snippet.

Every alert includes two probe fields that identify exactly how and why the failure was detected:

- **Probe Type** — short label classifying the detection mechanism
- **Detection** — full detail string with path, status code, or error message

| Failure | Probe Type | Example Detection value |
|---------|------------|-------------------------|
| `crashed` | `Docker event (crash)` | `Crash probe failed — container exited with exit code 1` |
| `oom` | `Docker event (OOM)` | `OOM probe failed — container was OOM-killed by the kernel` |
| `unhealthy` (Docker native HEALTHCHECK) | `Docker HEALTHCHECK` | `Docker health probe failed — exit code 1, output: connection refused` |
| `unhealthy` (HTTP auto-probe) | `HTTP GET` | `HTTP probe failed — error response 503 Service Unavailable, URI http://172.18.0.5:8080/health` |
| `unhealthy` (TCP connect) | `TCP connect` | `TCP probe failed — connection refused or timed out on 172.18.0.5:6379 (Connection refused)` |
| `unhealthy` (/proc fallback) | `/proc alive check` | `/proc alive check failed — PID 1 is zombie/stopped (exit code 1)` |

> **Restart-loop detection** uses Docker `RestartCount` from the polling loop as its sole restart count. The first poll establishes a baseline without interpreting historical restarts. Counter increases are timestamped when observed, so detection can take up to one polling interval; the rolling window is approximate to polling precision. After a polling gap longer than the restart window, unknown-age increments are ignored rather than misattributed to the current window. Delayed Docker events cannot double-count restarts; timestamps on events also prevent failures from before confirmed recovery from reopening an incident.

---

## 7. Verify Watchdog Operation

On an installed system, use these read-only checks:

```bash
docker ps --filter name=docker-container-watchdog
docker inspect docker-container-watchdog --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
docker logs --tail 50 docker-container-watchdog
```

Expected: the watchdog is running/healthy and monitoring logs are current. Check the configured notification receiver for any existing alerts. A healthy system does not need to generate a new alert just for verification.

Do not simulate failures on operational hosts. Developers can use the isolated release test procedure in [Release Smoke Testing](docs/RELEASE_SMOKE_TESTING.md).

## 8. Troubleshooting

### Service Fails to Start

```bash
docker compose logs docker-container-watchdog
```

Common causes:
- **Missing `.env` file** — complete the initial installer wizard, or restore it from a previous-installation backup
- **Docker socket not accessible** — ensure `/var/run/docker.sock` exists and the container has read access
- **Release image/package unavailable** — obtain the complete approved versioned package, verify `SHA256SUMS`, extract it and run `bash install.sh` (see [Deployment Guide](DEPLOYMENT.md))

### Alert Notifications Not Received

1. Check `alert_channels` in `watchdog-config.yaml` — ensure at least one channel is listed and configured
2. Check watchdog logs for errors:
   ```bash
   grep -E "ERROR|CRITICAL" watchdog/watchdog.log
   ```
3. For Slack: confirm `SLACK_WEBHOOK_URL` is present in `.env` and that the destination is reachable. Use an authorized test receiver if end-to-end validation is required; do not trigger application failures to test alert delivery.

**Recovery alert (`recovered`) specifically not received:**
- Confirm `alert_on_recovery` is not set to `false` in the live `watchdog-config.yaml`.
- Confirm the container was actually alarmed first — `state.alerted_for` must be non-empty; a container that was never flagged won't generate a recovery alert.
- For containers with a Docker `HEALTHCHECK`, confirm Docker actually reports `healthy` — `docker inspect <name> --format '{{json .State.Health}}'`. If it's stuck on `starting`, the watchdog is waiting on Docker, not the other way around.
- For containers on active probes (HTTP/TCP/`/proc`), confirm the poll loop reports `health=healthy` for the container in the log — if it stays `unhealthy`, review the actual endpoint and probe evidence with the responsible engineering team before changing monitoring.

### Duplicate and Excessive Alert Notifications

Review the affected container's alert history and probe details.

### Probe Type Not as Expected

Check which probe was cached at startup:

```bash
grep -E "auto-discover|falling back" watchdog/watchdog.log
```

### Viewing Logs

Log destinations (simultaneous):
- **stdout** — always on; captured by `docker compose logs`
- **file** — `./watchdog/watchdog.log` on the host (bind-mounted), rotates at 10 MB, keeps 5 backups
- **syslog** — remote server if `syslog.enabled: true` in config

```bash
# Live tail (preferred — host path, no need to exec into the container)
tail -f watchdog/watchdog.log

# Last 100 lines
tail -100 watchdog/watchdog.log

# All structured alerts, including INFO recoveries
grep 'watchdog_alert' watchdog/watchdog.log

# Errors and critical lines
grep -E "ERROR|CRITICAL" watchdog/watchdog.log

# Filter by container name
grep "my-container" watchdog/watchdog.log

# Search across rotated logs
grep "CRITICAL" watchdog/watchdog.log \
             watchdog/watchdog.log.1

# Via Docker (stdout only)
docker compose logs -f docker-container-watchdog
```

---

## 9. Container Probe Map (This Deployment)

Probe selection is automatic: containers with a Docker `HEALTHCHECK` are monitored passively via events; all others get an active probe auto-discovered on the first poll and cached for the container's lifetime.

| Container | Probe Type | Endpoint | Why |
|-----------|-----------|----------|-----|
| `config_kvision-infra-fluentd_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-dp-inline-config_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-collector_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-configuration-service_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-anomaly-detection-engine_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-data-persist-service_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-rt-alert_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_policy-editor_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-alerts_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-reporter_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-vrm_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-infra-redis_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-health_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-webui_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-tor-feed_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-infra-efk_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-data-polling-mgr_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-formatter_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-data-polling-scheduler_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-infra-cadvisor_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-scheduler_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-vdirect_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-help_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-ted_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_postgres_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-snmp-trap-collector_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-auto-engine_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-lls_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-assist-service_1` | HTTP | `:3005` (path auto-discovered) | No Docker HEALTHCHECK; Node.js service; path cached on first successful response |
| `config_kvision-infra-rabbitmq_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-ha-operator_1` | HTTP | `:8080` (path auto-discovered) | No Docker HEALTHCHECK; Java HTTP service; path cached on first successful response |
| `config_kvision-dc-nginx_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `process-exporter` | HTTP | `:9256/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `prometheus` | HTTP | `:9090/-/ready` | No Docker HEALTHCHECK; standard Prometheus readiness endpoint |
| `alertmanager` | HTTP | `:9093/-/healthy` | No Docker HEALTHCHECK; standard Alertmanager health endpoint |
| `postgres-exporter` | HTTP | `:9187/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `mysql-exporter` | HTTP | `:9104/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `es-exporter` | HTTP | `:9114/metrics` | No Docker HEALTHCHECK; port 7979 HTTP fails; port 9114 serves `/metrics` |
| `node-exporter` | HTTP | `:9100/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `grafana` | HTTP | `:3000/api/health` | No Docker HEALTHCHECK; Grafana health endpoint returns 200 |
| `config_kvision-infra-mariadb_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |

> **Verify what was actually cached after startup:**
> ```bash
> grep -E "auto-discover|falling back" watchdog/watchdog.log
> ```

## 10. Version History

**Version 1.6.0 (2026-10-15):** This section documents v1.6.0 and changes since v1.5.4. Release artifacts are built, tested and published separately; updating the source checkout does not upgrade running containers.

**Release procedure:** Merge reviewed release changes into `main`, then push the approved matching `vX.Y.Z` tag. GitHub Actions builds the image, assembles one complete customer package and its checksum, and publishes the [GitHub Release](https://github.com/Radware/CyberController-Container-Alert/releases). PR merges do **not** publish automatically. Follow the reusable [Development Guide](docs/DEVELOPMENT.md); version-specific approval for v1.6.0 is in its [finalization checklist](docs/RELEASE_V1.6.0_CHECKLIST.md).

| Version | Date | Author | Changes |
|---------|------------|--------|---------|
| 1.6.0 | 2026-10-15 | Radware engineering | Changes since 1.5.4: PRs [#3](https://github.com/Radware/CyberController-Container-Alert/pull/3), [#4](https://github.com/Radware/CyberController-Container-Alert/pull/4), [#5](https://github.com/Radware/CyberController-Container-Alert/pull/5), [#6](https://github.com/Radware/CyberController-Container-Alert/pull/6), [#7](https://github.com/Radware/CyberController-Container-Alert/pull/7), [#8](https://github.com/Radware/CyberController-Container-Alert/pull/8), [#10](https://github.com/Radware/CyberController-Container-Alert/pull/10); corrected restart-loop/recovery lifecycle, planned-stop/exit-137 handling, event ID/name reuse, independent optional HA failover detection, Syslog and notification consistency, one complete versioned release package and wizard, approved informational alert messages, and isolated-only release smoke tests. See [complete v1.6.0 release notes](docs/RELEASE_NOTES_V1.6.0.md). |
| 1.5.4 | 2026-09-23 | Rahul Kumar | Versioned Docker image (`watchdog:<VERSION>`) driven by the root `VERSION` file; installer loads the packaged `watchdog.tar`, rejects an archive that does not carry the release tag, aborts on Compose failures, and verifies the running image; restart-loop detection now uses Docker `RestartCount` instead of counting poll observations; `container_health_checks` overrides implemented; fixed `.env` migration on root-owned files; `watchdog-config.yaml` made readable by the non-root container user |
| 1.5.3 | 2026-09-16 | Rahul Kumar | Resource Limits Enforced |
| 1.5.2 | 2026-08-31 | Rahul Kumar | Updated error message"Suppressing expected Cyber Controller SQL dump syntax-check container termination (exit 137) alert"  | 
| 1.5.1 | 2026-08-31 | Rahul Kumar | fixed ignore Dynamic container crash alert |
| 1.5.0 | 2026-08-27 | Rahul Kumar | Added ignore Dynamic container crash alert |
| 1.4.0 | 2026-08-17 | Rahul Kumar | Added INFO "recovered" alert |
| 1.3.3 | 2026-08-05 | Rahul Kumar | Added Auth True/false |
| 1.3.2 | 2026-07-28 | Rahul Kumar | Fixed SMTP Auth issue |
| 1.3.1 | 2026-07-21 | Radware engineering | Updated Readme and Deployment guides |
| 1.3.0 | 2026-07-16 | Rahul Kumar | Added Upgrade Guide |
| 1.2.0 | 2026-07-16 | Rahul Kumar | Removed sudo and renamed the container |
| 1.1.0 | 2026-07-09 | Rahul Kumar | Added SNMPv3 support |
| 1.0.0 | 2026-06-25 | Rahul Kumar | Added Slack notifications and bug fixes |
