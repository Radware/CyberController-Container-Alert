# CyberController Container Watchdog — Deployment Guide

The **CyberController Container Watchdog** is an autonomous monitoring service that provides continuous health visibility across all Docker containers running on the host. It detects failures — including crashes, out-of-memory kills, prolonged unhealthy states, and restart loops — and dispatches real-time alerts through one or more configurable channels (**Slack**, **SMTP**, **SNMP Traps**, or **Syslog**).

This guide covers initial deployment, alert channel configuration, and ongoing operational management of the watchdog service.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Structure](#2-repository-structure)
3. [Configure Environment Variables](#3-configure-environment-variables)
4. [Configure the Watchdog](#4-configure-the-watchdog)
5. [Install and Deploy](#5-install-and-deploy)
6. [Verify Deployment](#6-verify-deployment)
7. [How to Set Up Communication Channels](#7-how-to-set-up-communication-channels)
8. [Logs](#8-logs)
9. [Alert Types](#9-alert-types)
10. [Configuration Reference](#10-configuration-reference)
11. [Lifecycle Management](#11-lifecycle-management)
12. [How to Change Existing Settings](#12-how-to-change-existing-settings)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum Version |
|---|---|
| Docker Engine | 20.10+ |
| Docker Compose plugin **or** standalone | v2 plugin (`docker compose`) or v1 standalone (`docker-compose`) |
| Linux host (for Docker socket) | Ubuntu 20.04 / RHEL 8 or later |

> **Docker Compose v1 vs v2:** All commands in this guide use the v2 syntax (`docker compose …`). If your system has the v1 standalone binary, replace `docker compose` with `docker-compose` throughout. To check which you have:
> ```bash
> docker compose version   # v2 plugin — "Docker Compose version v2.x.x"
> docker-compose --version # v1 standalone — "docker-compose version 1.x.x"
> ```

> **Alert channels:** At least one communication channel must be configured before the watchdog can send alerts. Choose from Slack, SMTP, SNMP Trap, or Syslog, then obtain the required credentials or endpoint details for your chosen channel(s) and configure them in `watchdog-config.yaml` and `.env`. See [How to Set Up Communication Channels](#7-how-to-set-up-communication-channels).


---

## 2. Repository Structure

```
Container_Alert/
├── docker-compose.yaml        # Production stack — uses pre-built image
├── docker-compose.build.yaml  # Developer build stack — requires internet
├── Dockerfile                 # Watchdog image definition
├── requirements-watchdog.txt  # Python deps: docker, requests, PyYAML
├── watchdog.py                # Watchdog agent
├── watchdog-config.yaml       # Watchdog behaviour & channel settings
├── install.sh                 # Install helper script
├── uninstall.sh               # Uninstall helper script
├── README.md                  # Quick-start reference
└── .env.example               # Template — copy to .env and fill in
```

---

## 3. Configure Environment Variables

```bash
cp .env.example .env
chmod 600 .env          # restrict to owner only — contains secrets
```

Open `.env` and fill in every value:

```dotenv
# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...

# Tuning (optional — defaults shown)
LOG_LEVEL=INFO
```
---

## 4. Configure the Watchdog

### Alert Channels

All alert channels are **optional** — configure at least one so the watchdog has somewhere to send alerts. Multiple channels can be active simultaneously; add each identifier to `alert_channels` in `watchdog-config.yaml`.

| Channel | `alert_channels` identifier | What you need |
|---|---|---|
| Slack Webhook | `slack` | Slack incoming webhook URL |
| Syslog | *(syslog config block)* | Syslog server reachable from the Docker host |
| Email (SMTP) | `smtp` | SMTP relay and credentials |
| SNMP Traps | `snmp_trap` | SNMP trap receiver (NMS / SIEM) |

> For step-by-step setup of each channel, see [How to Set Up Communication Channels](#7-how-to-set-up-communication-channels).

### Configuration File (watchdog-config.yaml)

Edit `watchdog-config.yaml` to match your environment:

```yaml
alert_channels:          # choose one or more: slack, smtp, snmp_trap
  - slack                # remove any channel you have not configured

check_interval_seconds: 60      # poll interval
cooldown_minutes: 5             # no duplicate alerts within this window
restart_threshold: 5            # restarts before "restart-loop" alert
restart_window_minutes: 10      # rolling window for restart counting
unhealthy_cycles_threshold: 3   # consecutive unhealthy polls before alert

excluded_containers:
  - debug-shell                 # add any containers you don't want monitored

runbook_base_url: "https://wiki.radware.internal/runbooks"

slack:
  webhook_url_env: SLACK_WEBHOOK_URL    # reads from .env
```

---

## 5. Install and Deploy

### Option A — Automated installation with `install.sh` (recommended)

Run the installer from the `Container_Alert/` directory. It guides you through every step interactively and requires no manual file editing.

> **Before running:** ensure `watchdog.tar` (the pre-built Docker image) is present in the same directory as `install.sh`. If it is missing the installer will offer to build the image from source (requires internet access).

```bash
cd /path/to/Container_Alert
sudo bash install.sh
```

The script walks through the following stages in order:

| Stage | What happens |
|---|---|
| **Prerequisites** | Verifies Docker and Docker Compose are installed and running |
| **Log directory** | Creates `./watchdog/` for persistent log storage |
| **Load image** | Loads `watchdog.tar` into Docker (`watchdog:latest`); builds from source if archive is absent |
| **Host identification** | `WATCHDOG_HOST` is hardcoded to `CyberController-Server` in `docker-compose.yaml` |
| **Credentials** | Uses existing `.env` if present; otherwise copies `.env.example` → `.env` with `chmod 600` |
| **Watchdog config** | Uses existing `watchdog-config.yaml` if present; otherwise writes one with safe defaults (all channels disabled) |
| **Start** | Runs `docker compose up -d` in the background |
| **Verify** | Checks the container is running and prints a summary with common commands |

> **Re-running `install.sh` on an existing installation is safe.** Existing configuration files (`.env`, `watchdog-config.yaml`) are preserved unchanged; only missing files are created from defaults.

---

### Option B — Manual installation

Use this path when you need full control over configuration files before starting.

#### 1. Load the Docker image

If `watchdog.tar` is present (pre-built offline image):

```bash
docker load -i watchdog.tar
```

If `watchdog.tar` is absent, build the image from source (requires internet):

```bash
docker build -t watchdog:latest .
```

#### 2. Create and secure the secrets file

```bash
cp .env.example .env
chmod 600 .env
```

Open `.env` and fill in the credentials for your chosen alert channel(s).

#### 3. Configure the watchdog

Edit `watchdog-config.yaml` — set `alert_channels`, thresholds, and any channel-specific blocks (see [How to Set Up Communication Channels](#7-how-to-set-up-communication-channels)).

#### 4. Start the container

```bash
docker compose up -d
```

#### 5. Verify

```bash
docker compose ps
docker compose logs -f watchdog
```

Expected output from `docker compose ps`:

```
NAME       STATUS
watchdog   Up (healthy)
```

---

### Option C — Deploy to air-gapped host

Use this when the target host has no internet access. Ensure `watchdog.tar` has already been transferred to the host before proceeding.

#### Step 1 — Load the image

```bash
docker load -i watchdog.tar
```

Verify the image loaded:

```bash
docker images watchdog
# Expected: watchdog   latest   <id>   <size>
```

#### Step 2 — Start the container

```bash
# v1 standalone
docker-compose up -d

# v2 plugin
docker compose up -d
```

#### Step 3 — Verify

```bash
docker ps --filter name=watchdog
docker-compose logs -f watchdog
```

---

## 6. Verify Deployment

### Check watchdog logs

```bash
docker compose logs -f watchdog
```

You should see:

```
Watchdog starting — host: cyber-controller-server
Docker event listener started
Poll loop started — interval: 60s
```

### Send a test alert

Force a container to exit with a non-zero code to trigger an alert:

```bash
docker run --name test-crash --rm alpine sh -c "exit 1"
```

Within 60 seconds (or immediately via the event listener) you should receive an alert in each of your configured channels.

---

## 7. How to Set Up Communication Channels

For each channel you want to use, follow the relevant option below. Ensure its identifier is listed under `alert_channels` in `watchdog-config.yaml`.

### Option A — Slack Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Container Watchdog`, select your workspace
3. In the left menu: **Incoming Webhooks** → toggle **On**
4. Click **Add New Webhook to Workspace** → choose an alert channel (e.g. `#container-alerts`)
5. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
6. Paste it into `.env` as `SLACK_WEBHOOK_URL`

---

### Option B — Syslog Forwarding

Enable the syslog block in `watchdog-config.yaml`:

```yaml
syslog:
  enabled: true
  host: 192.168.1.100      # your syslog server IP or hostname
  port: 514                # 514 = UDP standard; 601 = TCP reliable
  protocol: udp            # udp or tcp
  facility: local0         # local0–local7, daemon, user, etc.
```

Restart the watchdog to apply:

```bash
docker compose restart watchdog
```

---

### Option C — SMTP (Email)

Add `smtp` to `alert_channels` in `watchdog-config.yaml` and configure the block:

```yaml
smtp:
  enabled: true
  host: smtp.your-domain.com
  port: 587
  sender: alerts@your-domain.com
  recipients:
    - ops-team@your-domain.com
  tls: true
  password_env: SMTP_PASSWORD
```

Set `SMTP_PASSWORD` in `.env`:

```dotenv
SMTP_PASSWORD=your-smtp-password-or-app-token
```

---

### Option D — SNMP Traps

Add `snmp_trap` to `alert_channels` in `watchdog-config.yaml` and configure the block:

```yaml
snmp_trap:
  enabled: true
  host: <your-snmp-trap-receiver-ip>
  port: 162
  version: v2c                       # v1, v2c, or v3
  community: public                  # SNMPv1/v2c only; ignored for v3
  trap_oid: "1.3.6.1.6.3.1.1.5.4"   # replace with your enterprise OID
  # SNMPv3 only — add SNMP_V3_AUTH_KEY / SNMP_V3_PRIV_KEY to .env
  # v3_username:      watchdog-user
  # v3_auth_protocol: SHA  # MD5 or SHA
  # v3_auth_key_env:  SNMP_V3_AUTH_KEY
  # v3_priv_protocol: AES  # DES or AES
  # v3_priv_key_env:  SNMP_V3_PRIV_KEY
```

---

## 8. Logs

| Location | Description |
|---|---|
| `docker compose logs watchdog` | Live stdout logs |
| `./watchdog/watchdog.log` | Bind-mounted log file on the host (inside the `Container_Alert/` directory) |
| `/var/log/watchdog/watchdog.log` | Inside the container (rotated, max 10 MB × 5 files) |

To follow logs with timestamps:

```bash
docker compose logs -f --timestamps watchdog
```

To read the log file directly from the volume:

```bash
docker exec watchdog tail -f /var/log/watchdog/watchdog.log
```

---

## 9. Alert Types

| Failure Type | Severity | Trigger |
|---|---|---|
| `crashed` | CRITICAL | Container exited with non-zero exit code |
| `oom` | CRITICAL | Container was OOM-killed by the kernel |
| `unhealthy` | HIGH | Health probe failed for `unhealthy_cycles_threshold` consecutive cycles |
| `restart-loop` | HIGH | Container restarted ≥ `restart_threshold` times within `restart_window_minutes` |

Each alert includes: container name, container ID, host, failure type, timestamp, exit code (if applicable), recommended action, runbook URL, and the last 20 log lines.

---

## 10. Configuration Reference

All settings are in `watchdog-config.yaml`. No image rebuild is required for configuration changes — restart the watchdog container to apply updates:

```bash
docker compose restart watchdog
```

| Setting | Default | Description |
|---|---|---|
| `check_interval_seconds` | `60` | How often to poll all containers |
| `cooldown_minutes` | `5` | Suppress duplicate alerts per container |
| `restart_threshold` | `5` | Restarts within the window to trigger restart-loop alert |
| `restart_window_minutes` | `10` | Rolling window for restart counting |
| `unhealthy_cycles_threshold` | `3` | Consecutive unhealthy polls before alerting |
| `excluded_containers` | `[debug-shell, load-test]` | Containers that are never alerted on |
| `log_level` | `INFO` | Logging verbosity: `INFO` (recommended for production) or `DEBUG` |

### Host Identification in Alerts

Set `WATCHDOG_HOST` in `docker-compose.yaml` under the watchdog service environment:

```yaml
environment:
  WATCHDOG_HOST: my-server-name
```

---

## 11. Lifecycle Management

### Stopping the Service

```bash
# Pause the container — restartable with `docker compose start`
docker compose stop

# Stop and remove the container, keep the image and log files
docker compose down
```

### Restarting After Configuration Changes

```bash
# Pick up watchdog-config.yaml changes (no rebuild needed)
docker compose restart watchdog

# Re-read .env credential changes
docker compose up -d
```

### Updating the Watchdog Image

```bash
# Rebuild the image (requires internet)
docker build -t watchdog:latest .

# Redeploy
docker-compose up -d        # v1
docker compose up -d        # v2
```

---

## 12. How to Change Existing Settings

No image rebuild is required for any configuration change. The type of change determines the required command:

| Changed file | What to run |
|---|---|
| `watchdog-config.yaml` (thresholds, channels, exclusions) | `docker compose restart watchdog` |
| `.env` (secrets, credentials) | `docker compose up -d` |
| `docker-compose.yaml` (host name, resource limits) | `docker compose up -d` |

### Apply watchdog-config.yaml changes

```bash
docker compose restart watchdog
# or (v1 standalone)
docker-compose restart watchdog
```

### Apply .env changes (credentials / secrets)

```bash
docker compose up -d
# or (v1 standalone)
docker-compose up -d
```

### Apply docker-compose.yaml changes (container settings)

For example, edit the `WATCHDOG_HOST` value in `docker-compose.yaml`:

```yaml
environment:
  WATCHDOG_HOST: my-new-server-name
```

Then redeploy to take effect:

```bash
docker compose up -d
```

---

### Uninstalling

Run `uninstall.sh` from the `Container_Alert/` directory. Choose the option that matches how much you want removed:

| Scenario | Container removed | Image removed | Logs removed | Command |
|---|:---:|:---:|:---:|---|
| Standard (container removed; image + logs prompted) | ✓ | Prompted | Prompted | `sudo bash uninstall.sh` |
| Remove everything without prompts | ✓ | ✓ | ✓ | `sudo bash uninstall.sh --remove-all --force` |
| Remove container, keep image + logs | ✓ | ✗ | ✗ | `sudo bash uninstall.sh --keep-image --keep-logs` |
| Remove container + image, keep logs | ✓ | ✓ | ✗ | `sudo bash uninstall.sh --keep-logs --force` |

#### Standard interactive uninstall

```bash
sudo bash uninstall.sh
```

Shows a removal plan (container name, image size, log directory size), asks for confirmation, then prompts whether to delete log files. Safe default for manual runs.

#### Remove container and image, preserve logs

```bash
sudo bash uninstall.sh --keep-logs
```

Stops and removes the `watchdog` container and the `watchdog:latest` Docker image. The `./watchdog/` log directory is left intact so you can review historical logs later.

#### Remove everything including logs

```bash
sudo bash uninstall.sh --remove-all
```

Removes the container, image, and the entire `./watchdog/` log directory. Prompts for confirmation before proceeding.

#### Fully non-interactive (automation / CI pipelines)

```bash
sudo bash uninstall.sh --remove-all --force
```

Skips all confirmation prompts. Use in automated pipelines or when scripting repeated clean installs.

> `--keep-logs` and `--remove-all` are mutually exclusive and cannot be combined.

---

## 13. Troubleshooting

### Service Fails to Start

```bash
docker compose logs watchdog
```

Common causes:
- `/var/run/docker.sock` is not accessible — ensure the host socket exists and the container has read access
- Missing `.env` file — run `cp .env.example .env` and fill in values

### Alert Notifications Not Received

1. Verify `SLACK_WEBHOOK_URL` is set correctly in `.env`
2. Test the webhook manually:
   ```bash
   curl -X POST -H 'Content-type: application/json' \
     --data '{"text":"Watchdog test"}' \
     "$SLACK_WEBHOOK_URL"
   ```
   Expected response: `ok`

### Duplicate Alert Notifications

Increase `cooldown_minutes` in `watchdog-config.yaml`. The default is 5 minutes per container per failure type.

### Excessive Alert Volume

Add noisy containers to `excluded_containers` in `watchdog-config.yaml`:

```yaml
excluded_containers:
  - debug-shell
  - load-test
  - my-noisy-container
```
