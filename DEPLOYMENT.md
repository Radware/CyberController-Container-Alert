# CyberController Container Watchdog — Deployment Guide

The **CyberController Container Watchdog** is an autonomous monitoring service that provides continuous health visibility across all Docker containers running on the host. It detects failures — including crashes, out-of-memory kills, prolonged unhealthy states, and restart loops — and dispatches real-time alerts through one or more configurable channels (**Slack**, **SMTP**, **SNMP Traps**, or **Syslog**).

This guide covers initial deployment, alert channel configuration, and ongoing operational management of the watchdog service.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Structure](#2-repository-structure)
3. [Install and Deploy](#3-install-and-deploy)
4. [Verify Deployment](#4-verify-deployment)
5. [How to Set Up Communication Channels](#5-how-to-set-up-communication-channels)
6. [Logs](#6-logs)
7. [Alert Types](#7-alert-types)
8. [Configuration Reference](#8-configuration-reference)
9. [Lifecycle Management](#9-lifecycle-management)
10. [How to Change Existing Settings](#10-how-to-change-existing-settings)
11. [Upgrade Guide](#11-upgrade-guide)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum Version |
|---|---|
| Docker Engine | 20.10+ |
| Docker Compose plugin **or** standalone | v2 plugin (`docker compose`)
| Linux host (for Docker socket) | Ubuntu 20.04 / RHEL 8 or later |


> ```bash
> docker compose version   # v2 plugin — "Docker Compose version v2.x.x"
> ```




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



## 3. Install and Deploy

### Option A — Automated installation with `install.sh` (recommended)

Run the installer from the `Container_Alert/` directory. It guides you through every step interactively and requires no manual file editing.

> Note : clone the repository first and then run installer

> **Before running:** If internet access is available, no additional steps are required as the installer will automatically build the Docker image from source; otherwise, download the pre-built Docker image archive, watchdog.tar(https://radwareil.sharepoint.com/:f:/s/NAResidentEngineers/IgC4d3ALtPwvSrz4is6QWmnPATRCQ7g6BXbnbWO3Q0FFMSk?e=oj4WGS), from  and place it in the same directory as install.sh before starting the installation.

```bash
cd /path/to/Container_Alert
bash install.sh
```

The script walks through the following stages in order:

| Stage | What happens |
|---|---|
| **Prerequisites** | Verifies Docker and Docker Compose are installed and running |
| **Log directory** | Creates `./watchdog/` for persistent log storage |
| **Load image** | Loads `watchdog.tar` into Docker (`watchdog:latest`); builds from source if archive is absent |
| **Host identification** | `WATCHDOG_HOST` is hardcoded to `CyberController-Server` in `docker-compose.yaml` |
| **Configuration wizard** | Selects channels and prompts for Slack/SMTP/SNMP/Syslog values interactively |
| **Credentials + config write** | Generates/updates `.env` and `watchdog-config.yaml` from wizard answers |
| **Start** | Runs `docker compose up -d` in the background |
| **Verify** | Checks the container is running and prints a summary with common commands |

> **Re-running `install.sh` on an existing installation is safe.** If configuration files already exist, the installer asks whether to reconfigure from scratch. Choose `N` to keep existing files unchanged.

---

### Option B — Manual installation

Use this path when you need full control over configuration files before starting.

> **alert channels:** At least one communication channel must be configured before the watchdog can send alerts. Choose from Slack, SMTP, SNMP Trap, or Syslog, then obtain the required credentials or endpoint details for your chosen channel(s) and configure them in `watchdog-config.yaml` and `.env`. See [How to Set Up Communication Channels](#5-how-to-set-up-communication-channels).

#### 1. Load the Docker image

If `watchdog.tar` is present (pre-built offline image):
(https://radwareil.sharepoint.com/:f:/s/NAResidentEngineers/IgC4d3ALtPwvSrz4is6QWmnPATRCQ7g6BXbnbWO3Q0FFMSk?e=oj4WGS)

```bash
docker load -i watchdog.tar
```

```bash
docker build -t watchdog:latest .
```

#### 2. Configure Environment Variables

```bash
cp .env.example .env
chmod 600 .env          # restrict to owner only — contains secrets
```

Open `.env` and fill in every value:

```dotenv
# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...

# SMTP (only if SMTP channel is enabled)
SMTP_USERNAME/login email=your-smtp-username
SMTP_PASSWORD/key value=your-smtp-password

# Tuning (optional — defaults shown)
LOG_LEVEL=INFO
```
---

#### 3. Configure the Watchdog

### Alert Channels


| Channel | `alert_channels` identifier | What you need |
|---|---|---|
| Slack Webhook | `slack` | Slack incoming webhook URL |
| Syslog | *(syslog config block)* | Syslog server reachable from the Docker host |
| Email (SMTP) | `smtp` | SMTP relay and credentials |
| SNMP Traps | `snmp_trap` | SNMP trap receiver (NMS / SIEM) |



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

runbook_base_url: "https://test/runbooks"

slack:
  webhook_url_env: SLACK_WEBHOOK_URL    # reads from .env
```

---

#### 4. Configure the watchdog

Edit `watchdog-config.yaml` — set `alert_channels`, thresholds, and any channel-specific blocks (see [How to Set Up Communication Channels](#5-how-to-set-up-communication-channels)).

# instruction : clone the repository first and then run installer

#### 5. Start the container

```bash
docker compose up -d
```

#### 6. Verify

```bash
docker compose ps
docker compose logs -f docker-container-watchdog
```

Expected output from `docker compose ps`:

```
NAME                        STATUS
docker-container-watchdog   Up (healthy)
```

---

### Option C — Deploy to air-gapped host

Use this when the target host has no internet access. Ensure `watchdog.tar` has already been transferred to the host before proceeding.

#### 1 — Load the image

```bash
docker load -i watchdog.tar
```

Verify the image loaded:

```bash
docker images watchdog
# Expected: watchdog   latest   <id>   <size>
```

#### 2 — Start the container

```bash
docker compose up -d
```

```bash
docker compose version
```

#### 3 — verify

```bash
docker ps --filter name=watchdog
docker compose logs -f docker-container-watchdog
```

---

## 4. Verify Deployment

### Check watchdog logs

```bash
docker compose logs -f docker-container-watchdog
```

You should see:

```
Watchdog starting — host: CyberController-Server
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

## 5. How to Set Up Communication Channels

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
docker compose restart docker-container-watchdog
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
  username_env: SMTP_USERNAME
  recipients:
    - ops-team@your-domain.com
  tls: true
  password_env: SMTP_PASSWORD
```

Set SMTP credentials in `.env`:

```dotenv
SMTP_USERNAME=your-smtp-username-or-app-token
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
  version: v2c                             # v1, v2c, or v3
  community: public                        # SNMPv1/v2c only; ignored for v3
  trap_oid: "1.3.6.1.4.1.89.110.0.1"      # Radware enterprise container-alert notification OID
  # SNMPv3 only — add SNMP_V3_AUTH_KEY / SNMP_V3_PRIV_KEY to .env
  # v3_username:        watchdog-user
  # v3_auth_protocol:   SHA                # MD5 or SHA
  # v3_auth_key_env:    SNMP_V3_AUTH_KEY   # min 8 chars
  # v3_priv_protocol:   AES                # AES (recommended) | AES192 | AES256 | DES (weak)
  # v3_priv_key_env:    SNMP_V3_PRIV_KEY   # min 8 chars; requires auth key also set
  # v3_local_engine_id: ""                 # pin engine ID across restarts — see SNMPv3 note below
```

**Trap var-binds (enterprise OID arc `1.3.6.1.4.1.89.110`):**

| OID | Object | Value |
|-----|--------|-------|
| `1.3.6.1.4.1.89.110.1.1.0` | `cwHost` | Hostname of the alerting node |
| `1.3.6.1.4.1.89.110.1.2.0` | `cwSummary` | Human-readable alert summary |
| `1.3.6.1.4.1.89.110.1.3.0` | `cwContainerName` | Container name |
| `1.3.6.1.4.1.89.110.1.4.0` | `cwFailureType` | `crashed` / `oom` / `unhealthy` / `restart-loop` |
| `1.3.6.1.4.1.89.110.1.5.0` | `cwProbeDetail` | Probe failure detail |

**SNMPv3 notes:**

- **Engine ID:** On the first run the watchdog logs an auto-stable engine ID derived from the hostname (look for `SNMP engine ID: 0x...` in the logs). Copy it into `v3_local_engine_id` and register it in `snmptrapd.conf` with `createUser -e 0x<id> watchdog-user SHA "..." AES "..."`. This keeps the ID stable across container restarts.
- **Key length:** Both auth and priv passphrases must be at least 8 characters (RFC 3414 §11.2). The watchdog rejects shorter keys with a clear error.
- **No privacy-only mode:** Setting a priv key without an auth key is rejected — SNMPv3 has no `privNoAuth` security level. Both keys must be set together for encrypted traps.
- **Protocol names:** Auth: `MD5`, `SHA`, `SHA256`, `SHA384`, `SHA512`. Priv: `AES` (AES-128, recommended), `AES192`, `AES256`, `DES` (weak — logs a warning). An unrecognised name is rejected with an error listing the supported values.

---

## 6. Logs

| Location | Description |
|---|---|
| `docker compose logs docker-container-watchdog` | Live stdout logs |
| `./watchdog/watchdog.log` | Bind-mounted log file on the host (inside the `Container_Alert/` directory) |
| `/var/log/watchdog/watchdog.log` | Inside the container (rotated, max 10 MB × 5 files) |

To follow logs with timestamps:

```bash
docker compose logs -f --timestamps docker-container-watchdog
```

To read the log file directly from the volume:

```bash
docker exec docker-container-watchdog tail -f /var/log/watchdog/watchdog.log
```

---

## 7. Alert Types

| Failure Type | Severity | Trigger |
|---|---|---|
| `crashed` | CRITICAL | Container exited with non-zero exit code |
| `oom` | CRITICAL | Container was OOM-killed by the kernel |
| `unhealthy` | HIGH | Health probe failed for `unhealthy_cycles_threshold` consecutive cycles |
| `restart-loop` | HIGH | Container restarted ≥ `restart_threshold` times within `restart_window_minutes` |

Each alert includes: container name, container ID, host, failure type, timestamp, exit code (if applicable), recommended action, runbook URL, and the last 20 log lines.

---

## 8. Configuration Reference

All settings are in `watchdog-config.yaml`. No image rebuild is required for configuration changes — restart the watchdog container to apply updates:

```bash
docker compose restart docker-container-watchdog
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

## 9. Lifecycle Management

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
docker compose restart docker-container-watchdog

# Re-read .env credential changes
docker compose up -d
```

### Updating the Watchdog Image

```bash
# Rebuild the image (requires internet)
docker compose -f docker-compose.build.yaml build

# Redeploy using the production runtime file
docker compose up -d        
```

---

## 10. How to Change Existing Settings

No image rebuild is required for any configuration change. The type of change determines the required command:

| Changed file | What to run |
|---|---|
| `watchdog-config.yaml` (thresholds, channels, exclusions) | `docker compose restart docker-container-watchdog` |
| `.env` (secrets, credentials) | `docker compose up -d` |
| `docker-compose.yaml` (host name, resource limits) | `docker compose up -d` |

### Apply watchdog-config.yaml changes

```bash
docker compose restart docker-container-watchdog
```

### Apply .env changes (credentials / secrets)

```bash
docker compose up -d
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
| Standard (container removed; image + logs prompted) | ✓ | Prompted | Prompted | `bash uninstall.sh` |
| Remove everything without prompts | ✓ | ✓ | ✓ | `bash uninstall.sh --remove-all --force` |
| Remove container, keep image + logs | ✓ | ✗ | ✗ | `bash uninstall.sh --keep-image --keep-logs` |
| Remove container + image, keep logs | ✓ | ✓ | ✗ | `bash uninstall.sh --keep-logs --force` |

#### Standard interactive uninstall

```bash
bash uninstall.sh
```

Shows a removal plan (container name, image size, log directory size), asks for confirmation, then prompts whether to delete log files. Safe default for manual runs.

#### Remove container and image, preserve logs

```bash
bash uninstall.sh --keep-logs
```

Stops and removes the `watchdog` container and the `watchdog:latest` Docker image. The `./watchdog/` log directory is left intact so you can review historical logs later.

#### Remove everything including logs

```bash
bash uninstall.sh --remove-all
```

Removes the container, image, and the entire `./watchdog/` log directory. Prompts for confirmation before proceeding.

#### Fully non-interactive (automation / CI pipelines)

```bash
bash uninstall.sh --remove-all --force
```

Skips all confirmation prompts. Use in automated pipelines or when scripting repeated clean installs.

> `--keep-logs` and `--remove-all` are mutually exclusive and cannot be combined.

---

## 11. Upgrade Guide

There are two ways to upgrade the Watchdog application.

---

### Before You Begin (Recommended)

Backup your deployment-specific configuration files before upgrading.

```bash
mkdir -p backup

cp .env backup/.env.bak
cp watchdog-config.yaml backup/watchdog-config.yaml.bak
```

To restore the configuration later (if required):

```bash
cp backup/.env.bak .env
cp backup/watchdog-config.yaml.bak watchdog-config.yaml
```

---

### Option 1: Upgrade from Git Repository

#### Note : Requires internet connectivity

#### 1. Stop the running container

```bash
docker compose down
```

#### 2. Navigate to the project directory

```bash
cd Container_Alert
```

#### 3. Download the latest source code

If you do **not** want to keep any local changes:

```bash
git fetch origin
git reset --hard origin/main
```

If you want to keep your local commits:

```bash
git pull origin main
```

#### 4. Build the latest image

```bash
docker compose -f docker-compose.build.yaml build
```

#### 5. Start the updated container

```bash
docker compose up -d
```

#### 6. Verify

```bash
docker ps
docker compose logs docker-container-watchdog
```

---

### Option 2: Redeploy the Application(offline)

#### You can either obtain the latest archive from the RE team or download it directly from GitHub (https://github.com/Radware/CyberController-Container-Alert.git). Replace the existing files in the application directory, uninstall the current version, and then follow the standard deployment procedure to redeploy the application(#3-install-and-deploy).

---

### Automatic Upgrade

Automatic upgrades are **not enabled** by default.

Upgrade the application manually using one of the methods above.

If Watchdog is deployed from a private Docker registry, you can update by pulling the latest image:

```bash
docker compose pull
docker compose up -d
```

or

```bash
docker pull <registry>/watchdog:latest
docker compose up -d
```

---

### Checking the Installed Version

View the image used by the running container:

```bash
docker inspect docker-container-watchdog --format='{{.Config.Image}}'
```

View locally available Watchdog images:

```bash
docker images watchdog
```

## 12. Troubleshooting

### Service Fails to Start

```bash
docker compose logs docker-container-watchdog
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
