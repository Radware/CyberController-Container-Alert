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
   - [Configuration Keys](#configuration-keys)
   - [Environment Variables](#environment-variables)
4. [Supported Alert Channels](#4-supported-alert-channels)
   - [Slack](#slack)
   - [Syslog](#syslog)
   - [SMTP (Email)](#smtp-email)
   - [SNMP Traps](#snmp-traps)
5. [Deployment Instructions](#5-deployment-instructions)
6. [Failure Types & Severities](#6-failure-types--severities)
7. [Testing](#7-testing)
   - [OOM Simulation](#oom-simulation)
   - [Probe Testing](#probe-testing)
   - [Recovery Alert Test](#recovery-alert-test)
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

~~~yaml
ha_monitor:
  enabled: true
  exporter_url: http://127.0.0.1:9664/metrics
~~~

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
├── docker-compose.build.yaml    # Developer / online build
├── Dockerfile                   # Image build definition (python:3.11-slim)
├── requirements-watchdog.txt    # Python dependencies (reference; Dockerfile pip-installs inline)
├── install.sh                   # Offline install script
├── uninstall.sh                 # Stop + remove script
├── VERSION                       # Release version — drives the image tag and installer
├── watchdog.tar                  # Pre-built Docker image (provided, no internet needed)
└── .env                          # Secrets — NOT committed to git
```

---

### Image Size

| Item | Size |
|------|------|
| Docker image (`watchdog:<VERSION>`) | ~180 MB (python:3.11-slim base + dependencies) |
| Running container (memory) | ~50–80 MB baseline; capped at `mem_limit: 256m` in `docker-compose.yaml` |
| `watchdog.tar` export | ~170 MB |

> **Note:** The values above are approximate and may vary depending on the host operating system, Docker version, and installed dependencies. If usage approaches the 256 MB limit (check with `docker stats docker-container-watchdog`), raise `mem_limit`/`mem_reservation` in `docker-compose.yaml` rather than removing the limit — and raise `memswap_limit` to at least the new `mem_limit` in **both** `docker-compose.yaml` and `docker-compose.build.yaml`, since Docker rejects a config where `memswap_limit` is lower than `mem_limit`.

### Container Hardening

By default the container runs as **root** so `docker compose up` works with zero configuration — no host-specific group setup needed to read `/var/run/docker.sock`. [install.sh](install.sh) instead auto-detects the socket's GID and opts into running the container as **non-root UID/GID 1000** (joined to that group via `group_add`), with no manual input required; manual installs can do the same by setting `WATCHDOG_UID`/`WATCHDOG_GID`/`DOCKER_GID` in `.env` (see [DEPLOYMENT.md](DEPLOYMENT.md#22-configure-environment-variables-env)). Either way, the container is constrained by a read-only root filesystem, no extra Linux capabilities (`cap_drop: ALL`), `no-new-privileges`, and CPU/memory/PID caps (`cpus: "0.50"`, `pids_limit: 200`, `mem_limit: 256m`), which contain a leak or runaway condition to this container instead of the host. Note that access to `/var/run/docker.sock` is effectively host-root-equivalent for **any** user (root or not): anything that can reach the socket can create privileged containers or bind-mount the host filesystem, so a compromise of the watchdog process is **not** contained by these controls — treat `docker.sock` access as the primary trust boundary when deciding who/what can reach this host.


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

All non-secret settings live in `watchdog-config.yaml`. Secrets (webhook URLs, passwords) are stored in `.env` and never committed to version control. Restart the container after editing either file — no image rebuild is needed.

### Configuration Keys

| Key | Default | Description |
|-----|---------|-------------|
| `alert_channels` | `["slack"]` | Active destinations: `slack`, `smtp`, `snmp_trap` |
| `check_interval_seconds` | `60` | How often the poll loop runs |
| `cooldown_minutes` | `5` | Suppress duplicate alerts per container per failure type |
| `restart_threshold` | `5` | Restart count within window that triggers a restart-loop alert |
| `restart_window_minutes` | `10` | Rolling window for restart counting |
| `restart_recovery_stability_seconds` | `120` | Minimum uninterrupted running time before a recovered alert for automatically restarted crash/OOM/restart-loop incidents |
| `unhealthy_cycles_threshold` | `3` | Consecutive unhealthy cycles before alerting |
| `alert_on_recovery` | `true` | Send an INFO "recovered" alert once a previously-alarmed container returns to normal |
| `excluded_containers` | `[]` | Container names to never alert on |
| `ignored_container_events` | MariaDB syntax-check label rule | Label-based event suppressions for intentional temporary containers |
| `auto_health_check` | enabled | Probe containers reporting `health=none` by discovering an HTTP/TCP endpoint |
| `container_health_checks` | `{}` | Per-container probe overrides (`type: http` or `type: exec`) for `health=none` containers; takes precedence over `auto_health_check` |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `log_file` | `/var/log/watchdog/watchdog.log` | Bind-mounted to `./watchdog/watchdog.log` on host. Rotates at 10 MB, 5 backups. Set to `null` to disable |
| `state_file` | `/var/log/watchdog/state.json` | Persists open alerts and the host boot time so recovery and reboot reporting survive a restart. Set to `""` to disable |
| `boot_grace_seconds` | `180` | After a detected host reboot, hold alerts this long while services start, then send one `reboot-summary`. `0` alerts immediately and sends the summary on the first poll |
| `runbook_base_url` | — | URL included in every alert |

Preferred suppression: Temporary MariaDB HA syntax-check containers should be created with Docker label `com.radware.cybercontroller.role=mariadb-ha-syntax-check`. The watchdog ignores only configured events for containers carrying that explicit label; normal service containers still alert.

Temporary fallback: until that label exists, dynamic MariaDB dump helper containers are not alerted as crashes only when all identifying signals match together: image `kvision_infra_mariadb`, container name is not `config_kvision-infra-mariadb_1`, Docker `AutoRemove` is `true`, a mount contains `/mnt/cli/tmp/mariadb_dump.sql.gz`, and the exit code is `137`. The primary MariaDB container remains monitored.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_WEBHOOK_URL` | Yes (if Slack enabled) | Slack incoming webhook URL |
| `SMTP_USERNAME` | Yes (if SMTP enabled) | SMTP username or login email used for login |
| `SMTP_PASSWORD` | Yes (if SMTP enabled) | SMTP account password or api key value |
| `SNMP_V3_AUTH_KEY` | Yes (if SNMPv3 enabled) | SNMPv3 authentication passphrase — minimum 8 characters (RFC 3414) |
| `SNMP_V3_PRIV_KEY` | Yes (if SNMPv3 authPriv) | SNMPv3 privacy passphrase — minimum 8 characters; requires auth key also set |
| `WATCHDOG_CONFIG` | No | Path to config file (default: `/etc/watchdog/watchdog-config.yaml`) |
| `WATCHDOG_HOST` | No | Hostname shown in alerts (default: system hostname) |

---

## 4. Supported Alert Channels

Configure at least one channel and add its identifier to `alert_channels` in `watchdog-config.yaml`. Multiple channels can be active simultaneously.

### Slack

Add `slack` to `alert_channels` and set `SLACK_WEBHOOK_URL` in `.env`:

```yaml
slack:
  enabled: true
  webhook_url_env: SLACK_WEBHOOK_URL
```

### Syslog

```yaml
syslog:
  enabled: true
  host: <syslog-server-ip>
  port: 514
  protocol: udp       # udp or tcp
  facility: local0
```

Syslog is an **alert and error stream**, not a per-poll health feed. It sends
one structured, single-line event="watchdog_alert" record per emitted incident
(including INFO recoveries and reboot summaries), plus watchdog ERROR/CRITICAL
diagnostics. Routine INFO/WARNING polling messages stay local; per-container
poll output is available at log_level: DEBUG. The alert records include the
severity, UTC time, host, container, failure type, detection evidence and action,
with special previous/new active-node fields for HA. The syslog priority is
CRITICAL=2, HIGH=3 (error), WARNING=4 and INFO=6; the facility remains
configurable. Values are quoted, escaped and length-limited for ingestion.
Syslog is configured independently from alert_channels.
Local log_level controls only console/file verbosity; INFO recoveries and
WARNING HA alerts still reach Syslog when local log_level is WARNING or ERROR.
HA and reboot-summary records omit pseudo-container fields.


### SMTP (Email)

Add `smtp` to `alert_channels` to enable.

```yaml
smtp:
  enabled: true
  host: smtp.radware.com
  port: 587
  sender: noc-alerts@radware.com
  username_env: SMTP_USERNAME     # env var: SMTP username or app token
  recipients:
    - ops-team@radware.com
    - oncall@radware.com
  tls: true
  password_env: SMTP_PASSWORD        # env var: SMTP password or app token
```

### SNMP Traps

Add `snmp_trap` to `alert_channels` to enable. Sends SNMP traps (v1, v2c, or v3) to your NMS/SIEM on every alert.

```yaml
snmp_trap:
  enabled: true
  host: 155.1.1.4                          # IP or hostname of your SNMP trap receiver
  port: 162                                # Standard SNMP trap port
  version: v2c                             # SNMP version: v1, v2c, or v3
  community: public                        # SNMPv1/v2c community string (ignored for v3)
  trap_oid: "1.3.6.1.4.1.89.110.0.1"      # Radware enterprise container-alert notification OID
  # SNMPv3 only — add SNMP_V3_AUTH_KEY / SNMP_V3_PRIV_KEY to .env
  # v3_username:        watchdog-user
  # v3_auth_protocol:   SHA                # MD5 | SHA | SHA256 | SHA384 | SHA512
  # v3_auth_key_env:    SNMP_V3_AUTH_KEY   # min 8 chars
  # v3_priv_protocol:   AES                # AES (recommended) | AES192 | AES256 | DES (weak)
  # v3_priv_key_env:    SNMP_V3_PRIV_KEY   # min 8 chars; requires auth key also set
  # v3_local_engine_id: ""                 # hex engine ID pinned from first-run log; see DEPLOYMENT.md
```

SNMP retains its five existing var-binds and OIDs for NMS compatibility.
The human-readable cwSummary contains severity; cwProbeDetail contains
detection evidence. No additional SNMP objects are introduced in v1.6.0.

Each trap carries five var-binds, all under the Radware enterprise OID arc (`1.3.6.1.4.1.89.110`):

| OID | Object | Value |
|-----|--------|-------|
| `1.3.6.1.4.1.89.110.1.1.0` | `cwHost` | Hostname of the alerting node |
| `1.3.6.1.4.1.89.110.1.2.0` | `cwSummary` | Human-readable alert summary |
| `1.3.6.1.4.1.89.110.1.3.0` | `cwContainerName` | Container name |
| `1.3.6.1.4.1.89.110.1.4.0` | `cwFailureType` | `crashed` / `oom` / `unhealthy` / `restart-loop` / `recovered` |
| `1.3.6.1.4.1.89.110.1.5.0` | `cwProbeDetail` | Probe failure detail |

---

## 5. Deployment Instructions

For full deployment instructions — including alert channel setup, offline installation, developer builds, and troubleshooting — see [DEPLOYMENT.md](DEPLOYMENT.md).

**Quick start (automated):**

```bash
bash install.sh
```

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

## 7. Testing

**Production / CyberController safety boundary:** Do **not** simulate crashes, OOM
events, restart loops, or recovery by running `docker kill`, `docker stop`,
`docker restart`, disposable stress containers, or other destructive experiments
on a CyberController node or another shared/operational Docker Engine.
Even supposedly disposable test containers can consume shared resources or
trigger real monitoring, and a nested Docker-in-Docker container remains visible
to the host's normal watchdog. The earlier host-based examples were removed
for this reason.

### Safe verification on an operational host (read-only)

Authorized operators may review the existing watchdog's current health and
its own recent logs without altering any container:

```bash
docker inspect docker-container-watchdog   --format 'state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker logs --tail 50 docker-container-watchdog
```

To validate an alert without changing a running service, use the unit/format
regression tests on a development workstation or the normal pull-request CI.
No failure injection is required for message formatting or severity checks.

### Destructive real Docker acceptance — disposable hosted runner ONLY

Run the **Release smoke (isolated)** GitHub Actions workflow on the intended
release branch (`workflow_dispatch`); it uses an ephemeral GitHub-hosted worker
with an empty, specially labelled Docker Engine, separate alert state, and a
temporary UDP Syslog receiver. The smoke runner aborts before creating any
test resource unless it verifies the dedicated hosted worker, exact localhost
test endpoint, unique engine label, and zero existing containers. **There is no
live-host override.**

The [smoke-test procedure](docs/RELEASE_SMOKE_TESTING.md) details acceptance
criteria, evidence capture, cleanup, and use of the workflow. The normal PR CI
runs only non-destructive Python, shell and build validations; destructive Docker
scenarios require manual dispatch of the separate hosted workflow.

Do not start a test Engine on CyberController or m920q. The previous 2026-10-14
m920q Docker-in-Docker test inadvertently generated real host watchdog alerts;
that approach must not be repeated.

## 8. Troubleshooting

### Service Fails to Start

```bash
docker compose logs docker-container-watchdog
```

Common causes:
- **Missing `.env` file** — run `cp .env.example .env` and fill in credentials
- **Docker socket not accessible** — ensure `/var/run/docker.sock` exists and the container has read access
- **Image not loaded** — run `docker images watchdog`; if empty, set `WATCHDOG_VERSION=$(cat VERSION)` and build with `WATCHDOG_VERSION="$WATCHDOG_VERSION" docker compose -f docker-compose.build.yaml build` (see [DEPLOYMENT.md](DEPLOYMENT.md) for details)

### Alert Notifications Not Received

1. Check `alert_channels` in `watchdog-config.yaml` — ensure at least one channel is listed and configured
2. Check watchdog logs for errors:
   ```bash
   grep -E "ERROR|CRITICAL" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log
   ```
3. For Slack: verify `SLACK_WEBHOOK_URL` is set in `.env` and test manually:
   ```bash
   curl -X POST -H 'Content-type: application/json' \
     --data '{"text":"Watchdog test"}' "$SLACK_WEBHOOK_URL"
   ```
   Expected response: `ok`

**Recovery alert (`recovered`) specifically not received:**
- Confirm `alert_on_recovery` is not set to `false` in the live `watchdog-config.yaml`.
- Confirm the container was actually alarmed first — `state.alerted_for` must be non-empty; a container that was never flagged won't generate a recovery alert.
- For containers with a Docker `HEALTHCHECK`, confirm Docker actually reports `healthy` — `docker inspect <name> --format '{{json .State.Health}}'`. If it's stuck on `starting`, the watchdog is waiting on Docker, not the other way around.
- For containers on active probes (HTTP/TCP/`/proc`), confirm the poll loop reports `health=healthy` for the container in the log — if it stays `unhealthy`, the probe (often `auto_health_check`) is misjudging the service; add a manual entry under `container_health_checks` instead.

### Duplicate and Excessive Alert Notifications

Increase `cooldown_minutes` in `watchdog-config.yaml` (default: `5` minutes per container per failure type). To silence a noisy container entirely, add it to `excluded_containers`:

```yaml
excluded_containers:
  - debug-shell
  - load-test
  - my-noisy-container
```

### Probe Type Not as Expected

Check which probe was cached at startup:

```bash
grep -E "auto-discover|falling back" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log
```

### Viewing Logs

Log destinations (simultaneous):
- **stdout** — always on; captured by `docker compose logs`
- **file** — `./watchdog/watchdog.log` on the host (bind-mounted), rotates at 10 MB, keeps 5 backups
- **syslog** — remote server if `syslog.enabled: true` in config

```bash
# Live tail (preferred — host path, no need to exec into the container)
tail -f /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Last 100 lines
tail -100 /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Alerts and errors only
grep -E "ALERT|ERROR|CRITICAL" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Filter by container name
grep "my-container" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Search across rotated logs
grep "CRITICAL" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log \
             /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log.1

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
> grep -E "auto-discover|falling back" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log
> ```

## 10. Version History

**Release status (2026-10-15):** `VERSION` remains `1.6.0` for the **upcoming, untagged** release candidate. PR #9 is open. The complete changes since 1.5.4, approved customer-facing alert wording, release acceptance and destructive-test isolation are documented in [v1.6.0 release notes](docs/RELEASE_NOTES_V1.6.0.md). Historical HA and isolated Docker tests passed on earlier candidates; the final hosted-runner safety workflow must pass against the latest commit before publication. No v1.6.0 tag or final offline package is published; deployed lab images are not automatically updated by this branch.

**Release procedure:** After validating a release candidate, increment `VERSION`, add a dated entry here, build and verify `watchdog:<VERSION>`, and package the matching `watchdog.tar` for offline installs. Pin deployment with `WATCHDOG_VERSION` in `.env`; verify upgrade and rollback before tagging the exact released commit (`vX.Y.Z`) and publishing release notes. Do not overwrite an old versioned image with new code. GitHub CI checks PRs but does not publish releases automatically; as of this update, no Git tags or GitHub Releases exist.

| Version | Date | Author | Changes |
|---------|------------|--------|---------|
| 1.6.0 | 2026-10-15 | Radware engineering | Upcoming since 1.5.4: PRs [#3](https://github.com/Radware/CyberController-Container-Alert/pull/3), [#4](https://github.com/Radware/CyberController-Container-Alert/pull/4), [#5](https://github.com/Radware/CyberController-Container-Alert/pull/5), [#6](https://github.com/Radware/CyberController-Container-Alert/pull/6), [#7](https://github.com/Radware/CyberController-Container-Alert/pull/7), [#8](https://github.com/Radware/CyberController-Container-Alert/pull/8), [#10](https://github.com/Radware/CyberController-Container-Alert/pull/10); corrected restart-loop/recovery lifecycle, planned-stop/exit-137 handling, event ID/name reuse, independent optional HA failover detection, Syslog and notification consistency, installer fallback, approved informational alert messages, and isolated-only release smoke tests. See [complete v1.6.0 release notes](docs/RELEASE_NOTES_V1.6.0.md). |
| 1.5.4 | 2026-09-23 | Rahul Kumar | Versioned Docker image (`watchdog:<VERSION>`) driven by the root `VERSION` file; installer loads the packaged `watchdog.tar`, rejects an archive that does not carry the release tag, aborts on Compose failures, and verifies the running image; restart-loop detection now uses Docker `RestartCount` instead of counting poll observations; `container_health_checks` overrides implemented; fixed `.env` migration on root-owned files; `watchdog-config.yaml` made readable by the non-root container user |
| 1.5.3 | 2026-09-16 | Rahul Kumar | Resource Limits Enforced |
| 1.5.2 | 2026-08-31 | Rahul Kumar | Updated error message"Suppressing expected Cyber Controller SQL dump syntax-check container termination (exit 137) alert"  | 
| 1.5.1 | 2026-08-31 | Rahul Kumar | fixed ignore Dynamic container crash alert |
| 1.5.0 | 2026-08-27 | Rahul Kumar | Added ignore Dynamic container crash alert |
| 1.4.0 | 2026-08-17 | Rahul Kumar | Added INFO "recovered" alert |
| 1.3.3 | 2026-08-05 | Rahul Kumar | Added Auth True/false |
| 1.3.2 | 2026-07-28 | Rahul Kumar | Fixed SMTP Auth issue |
| 1.3.1 | 2026-07-21 | Egor Egorov | Updated Readme and Deployment guides |
| 1.3.0 | 2026-07-16 | Rahul Kumar | Added Upgrade Guide |
| 1.2.0 | 2026-07-16 | Rahul Kumar | Removed sudo and renamed the container |
| 1.1.0 | 2026-07-09 | Rahul Kumar | Added SNMPv3 support |
| 1.0.0 | 2026-06-25 | Rahul Kumar | Added Slack notifications and bug fixes |
