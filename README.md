# CyberController Container Watchdog

The **CyberController Container Watchdog** is a lightweight, autonomous monitoring service that continuously tracks the health of all Docker containers on the host. It detects failures — including crashes, out-of-memory kills, prolonged unhealthy states, and restart loops — and dispatches real-time alerts through one or more configurable channels (**Slack**, **SMTP**, **SNMP Traps**, or **Syslog**).

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [System Overview](#system-overview)
3. [Configuration](#configuration)
4. [Supported Alert Channels](#supported-alert-channels)
5. [Deployment Instructions](#deployment-instructions)
6. [Failure Types & Severities](#failure-types--severities)
7. [Testing](#testing)
8. [Troubleshooting](#troubleshooting)
9. [Container Probe Map](#container-probe-map)

---

## How It Works

The watchdog runs two parallel monitoring paths:

| Path | Mechanism | What It Catches |
|------|-----------|-----------------|
| **Event stream** | `docker events` (real-time) | Crashes (`die`), OOM-kills (`oom`), health status changes |
| **Poll loop** | `docker ps` every N seconds | Stuck unhealthy containers, auto health checks, restart loops (≥ 5 restarts in 10 min by default) |

The watchdog selects one of **4 active probe types** per container, chosen automatically based on the container's configuration. Probe selection is cached for the container's lifetime and re-discovered on restart.

1. **Docker HEALTHCHECK** *(passive)* — for containers that have a `HEALTHCHECK` directive in their Dockerfile. The watchdog reads the result from `container.attrs["State"]["Health"]` rather than running its own probe. When Docker reports `health_status: unhealthy` 3 consecutive times, an alert fires. The last health check output is included in the alert's Detection field.

2. **HTTP GET** *(active)* — for containers with **no** `HEALTHCHECK` that expose HTTP ports. Auto-discovers a working endpoint by trying common paths (`/health`, `/healthz`, `/-/healthy`, `/metrics`, `/`) on each exposed internal port. Returns healthy if `status_code < 500`. The discovered URL is cached for the container's lifetime.

3. **TCP connect** *(active)* — if no HTTP path responds on a port, falls back to a raw `socket.create_connection()` on that port. A successful connect confirms the service is listening; a refused or timed-out connection triggers an `unhealthy` alert. Used by databases, message brokers, and any non-HTTP service.

4. **`/proc` alive check** *(active)* — last resort when the container exposes no ports at all, or all TCP connects fail during initial discovery. Reads `/proc/1/status` inside the container via `exec_run` and checks that PID 1 is in a live state (R/S/D). A zombie (Z) or stopped (T) PID 1 triggers an `unhealthy` alert.

Alerts are deduplicated via a **cooldown** — once an alert fires for a container, the same failure type is suppressed for `cooldown_minutes` minutes. The cooldown resets automatically when the container recovers.

---

## System Overview

### Project Structure

```
.
├── watchdog.py                  # Main monitor script
├── watchdog-config.yaml         # All configuration (no secrets)
├── docker-compose.yaml          # Production / offline runtime (image only)
├── docker-compose.build.yaml    # Developer / online build
├── Dockerfile                   # Image build definition (python:3.11-slim)
├── requirements-watchdog.txt    # Python dependencies (reference; Dockerfile pip-installs inline)
├── install.sh                   # Offline install script
├── uninstall.sh                 # Stop + remove script
├── watchdog.tar                 # Pre-built Docker image (provided, no internet needed)
└── .env                         # Secrets — NOT committed to git
```

---

### Image Size

| Item | Size |
|------|------|
| Docker image (`watchdog:latest`) | ~180 MB (python:3.11-slim base + dependencies) |
| Running container (memory) | ~50–80 MB |
| `watchdog.tar` export | ~170 MB |

> These are approximate. Run `docker image ls watchdog` and `docker stats watchdog` after deployment to see exact values on your host.

### Dependencies

```
docker==7.1.0
requests==2.32.3
PyYAML==6.0.2
pysnmp>=4.4.12
```

Python 3.11+

---

## Configuration

All non-secret settings live in `watchdog-config.yaml`. Secrets (webhook URLs, passwords) are stored in `.env` and never committed to version control. Restart the container after editing either file — no image rebuild is needed.

### Configuration File (watchdog-config.yaml)

| Key | Default | Description |
|-----|---------|-------------|
| `alert_channels` | `[]` | Active destinations: `slack`, `smtp`, `snmp_trap` |
| `check_interval_seconds` | `60` | How often the poll loop runs |
| `cooldown_minutes` | `5` | Suppress duplicate alerts per container per failure type |
| `restart_threshold` | `5` | Restart count within window that triggers a restart-loop alert |
| `restart_window_minutes` | `10` | Rolling window for restart counting |
| `unhealthy_cycles_threshold` | `3` | Consecutive unhealthy cycles before alerting |
| `excluded_containers` | `[]` | Container names to never alert on |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `log_file` | `/var/log/watchdog/watchdog.log` | Bind-mounted to `./watchdog/watchdog.log` on host. Rotates at 10 MB, 5 backups. Set to `null` to disable |
| `runbook_base_url` | — | URL included in every alert |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_WEBHOOK_URL` | Yes (if Slack enabled) | Slack incoming webhook URL |
| `SMTP_USERNAME` | Yes (if SMTP enabled) | SMTP username or app token used for login |
| `SMTP_PASSWORD` | Yes (if SMTP enabled) | SMTP account password or app token |
| `WATCHDOG_CONFIG` | No | Path to config file (default: `/etc/watchdog/watchdog-config.yaml`) |
| `WATCHDOG_HOST` | No | Hostname shown in alerts (default: system hostname) |

---

## Supported Alert Channels

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

### SMTP (Email)

Add `smtp` to `alert_channels` to enable.

```yaml
smtp:
  enabled: true
  host: smtp.radware.com
  port: 587
  sender: noc-alerts@radware.com
  username_env: SMTP_USERNAME        # env var: SMTP username or app token
  recipients:
    - ops-team@radware.com
    - oncall@radware.com
  tls: true
  password_env: SMTP_PASSWORD        # env var: SMTP password or app token
```

### SNMP Traps

Add `snmp_trap` to `alert_channels` to enable. Sends SNMPv2c traps to your NMS/SIEM on every alert.

```yaml
snmp_trap:
  enabled: true
  host: 155.1.1.4                    # IP or hostname of your SNMP trap receiver
  port: 162                          # Standard SNMP trap port
  version: v2c                       # SNMP version: v1, v2c, or v3
  community: public                  # SNMPv1/v2c community string (ignored for v3)
  trap_oid: "1.3.6.1.6.3.1.1.5.4"   # Replace with your enterprise OID
  # SNMPv3 only — add SNMP_V3_AUTH_KEY / SNMP_V3_PRIV_KEY to .env
  # v3_username:      watchdog-user
  # v3_auth_protocol: SHA              # MD5 or SHA
  # v3_auth_key_env:  SNMP_V3_AUTH_KEY
  # v3_priv_protocol: AES              # DES or AES
  # v3_priv_key_env:  SNMP_V3_PRIV_KEY
```

---

## Deployment Instructions

For full deployment instructions — including alert channel setup, offline installation, developer builds, and troubleshooting — see [DEPLOYMENT.md](DEPLOYMENT.md).

**Quick start (automated):**

```bash
sudo bash install.sh
```

---

## Failure Types & Severities

| Failure | Severity | Trigger |
|---------|----------|---------|
| `crashed` | CRITICAL | Container exited with non-zero exit code |
| `oom` | CRITICAL | Container was OOM-killed by the kernel |
| `unhealthy` | HIGH | Health probe failing for N consecutive cycles |
| `restart-loop` | HIGH | Container restarted ≥ threshold times within window |

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

> **Restart-loop detection** is not a probe — it is tracked by the **Poll loop** path and fires when a container restarts ≥ `restart_threshold` times within `restart_window_minutes`. See the Poll loop row in the monitoring paths table above.

---

## Testing

The sections below describe optional manual tests that validate the watchdog's detection and alerting paths. Each creates a temporary Docker container and cleans up after itself.

### OOM Simulation

Two options — choose based on whether the host has internet access.

#### Option A — `polinux/stress` *(requires internet access)*

> Pulls `polinux/stress` from Docker Hub.

```bash
# --memory-swap=32m disables swap so the kernel is forced to OOM-kill rather than swap out
# Note: the polinux/stress entrypoint IS the stress binary — do not repeat "stress" in the args
docker run -d --name oom-test --memory=32m --memory-swap=32m polinux/stress \
  --vm 1 --vm-bytes 64M --vm-keep
```

```bash
# Clean up
docker rm -f oom-test
docker rmi polinux/stress
```

#### Option B — `watchdog:latest` *(no internet required)*

> Uses the watchdog image already present on the host — no pull needed.

```bash
docker run -d --name oom-test --memory=32m --memory-swap=32m watchdog:latest \
  python3 -c "x = b'\xff' * (64 * 1024 * 1024)"
```

```bash
# Clean up — container only (keep the watchdog image)
docker rm -f oom-test
```

Docker kills the container with exit code `137` and emits an `oom` event. The watchdog captures memory stats and the last 20 log lines, then dispatches a CRITICAL alert — all within seconds of the kill.

> **If the container exits cleanly (exit code 0) instead of being OOM-killed**, cgroup memory accounting may be disabled on this host. Verify with:
> ```bash
> docker info 2>&1 | grep -i "memory limit"
> ```
> `WARNING: No memory limit support` means the `--memory` flag is silently ignored. Enable cgroup memory accounting by adding `cgroup_enable=memory swapaccount=1` to `GRUB_CMDLINE_LINUX` in `/etc/default/grub`, then run `sudo update-grub` and reboot.

---

### Probe Testing *(Optional — requires internet access)*

> These tests pull images from Docker Hub and create temporary containers on the host.
> Each section includes full cleanup instructions (container + image).

#### HTTP Probe

```bash
# Start a container where every HTTP path returns 503
# Note: the full server { } block is required — a bare location { } directive is not valid nginx config
docker run -d --name http-probe-test \
  nginx:alpine \
  sh -c "printf 'server { listen 80; location / { return 503; } }' > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"

# Verify the container is running:
docker ps | grep http-probe-test

# Wait one poll cycle (60s), then check watchdog detected it:
grep "http-probe-test" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Clean up
docker rm -f http-probe-test
docker rmi nginx:alpine
```

During auto-discovery the watchdog sends an HTTP GET to `/-/healthy` on port 80. Nginx returns 503, so the watchdog immediately caches that endpoint as failing and reports unhealthy. After `unhealthy_cycles_threshold` consecutive unhealthy cycles an alert fires with Detection value `HTTP probe failed — error response 503 Service Unavailable, URI http://<ip>:80/-/healthy`.

---

#### TCP Connect Probe

```bash
# Expose port 9999 but run nothing on it — TCP connect will be refused
docker run -d --name tcp-probe-test \
  --expose 9999 \
  alpine \
  sh -c "sleep infinity"

# Watch the watchdog fall through HTTP → TCP and alert:
grep "tcp-probe-test" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Clean up
docker rm -f tcp-probe-test
docker rmi alpine
```

After `unhealthy_cycles_threshold` consecutive failed cycles an alert fires with Detection value `TCP probe failed — connection refused or timed out on <ip>:9999`.

---

#### `/proc` Alive Check

```bash
# No EXPOSE — watchdog falls straight to /proc alive check
docker run -d --name proc-probe-test \
  alpine \
  sleep infinity

# Verify the container is running:
docker ps | grep proc-probe-test

# After one poll cycle, confirm the watchdog selected /proc as the probe type:
grep "proc-probe-test" /opt/radware/storage/scripts/Alert_Container/watchdog/watchdog.log

# Clean up
docker rm -f proc-probe-test
docker rmi alpine
```

With no exposed ports, auto-discovery skips HTTP and TCP and falls back to reading `/proc/1/status` inside the container via `exec_run`. Since `sleep` is PID 1 in a live `S` (sleeping) state, no alert fires. The log will show `falling back to /proc alive-check` confirming the probe type was selected. In production, this probe fires when PID 1 enters zombie (`Z`) or stopped (`T`) state.

---

## Troubleshooting

### Service Fails to Start

```bash
docker compose logs watchdog
# or (v1 standalone)
docker-compose logs watchdog
```

Common causes:
- **Missing `.env` file** — run `cp .env.example .env` and fill in credentials
- **Docker socket not accessible** — ensure `/var/run/docker.sock` exists and the container has read access
- **Image not loaded** — run `docker images watchdog`; if empty, build with `docker build -t watchdog:latest .`

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
docker compose logs -f watchdog
```

---

## Container Probe Map (This Deployment)

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
