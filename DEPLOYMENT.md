# CyberController Container Watchdog — Deployment Guide

The **CyberController Container Watchdog** is an autonomous monitoring service that provides continuous health visibility across all Docker containers running on the host. It detects failures — including crashes, out-of-memory kills, prolonged unhealthy states, and restart loops — and dispatches real-time alerts through one or more configurable channels (**Slack**, **SMTP**, **SNMP Traps**, or **Syslog**).

This guide covers initial deployment, alert channel configuration, and ongoing operational management of the watchdog service.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
   - [1.1 Pre-flight Checks](#11-pre-flight-checks)
2. [Environment Setup](#2-environment-setup)
   - [2.1 Clone the Repository](#21-clone-the-repository)
   - [2.2 Configure Environment Variables (.env)](#22-configure-environment-variables-env)
   - [2.3 Configure Watchdog Global Settings](#23-configure-watchdog-global-settings)
   - [2.4 Configure Alert Channels](#24-configure-alert-channels)
3. [Deployment Steps](#3-deployment-steps)
   - [3.1 Option A: Automated Installation (install.sh) (Recommended)](#31-option-a-automated-installation-installsh-recommended)
   - [3.2 Option B: Manual Installation](#32-option-b-manual-installation)
4. [Verification](#4-verification)
   - [4.1 Confirm Container Status and Health](#41-confirm-container-status-and-health)
   - [4.2 Reviewing Logs](#42-reviewing-logs)
5. [Rollback](#5-rollback)
   - [5.1 Roll Back a Failed Deployment](#51-roll-back-a-failed-deployment)
   - [5.2 Roll Back an Image Upgrade](#52-roll-back-an-image-upgrade)
   - [5.3 Roll Back an Application Upgrade (Git)](#53-roll-back-an-application-upgrade-git)
6. [Operations and Maintenance](#6-operations-and-maintenance)
   - [6.1 Lifecycle Management](#61-lifecycle-management)
   - [6.2 Applying Configuration Changes](#62-applying-configuration-changes)
   - [6.3 Upgrade Guide](#63-upgrade-guide)
   - [6.4 Uninstalling](#64-uninstalling)
7. [Troubleshooting](#7-troubleshooting)
8. [Appendix and Reference](#8-appendix-and-reference)
   - [8.1 Repository Structure](#81-repository-structure)
   - [8.2 Configuration Reference](#82-configuration-reference)
   - [8.3 SNMP Trap Var-Binds](#83-snmp-trap-var-binds)
   - [8.4 Support Contacts](#84-support-contacts)

---

## 1. Prerequisites

| Requirement | Minimum Version / Notes |
|---|---|
| Docker Engine | 20.10+ |
| Docker Compose | v2 plugin (`docker compose`) or standalone `docker-compose` |
| Linux host | Ubuntu 20.04 / RHEL 8 or later (required for Docker socket access) |
| Git | Required to clone the repository |
| Host permissions | Root, or membership in the `docker` group (to read `/var/run/docker.sock`) |

```bash
docker compose version
```

### 1.1 Pre-flight Checks

Run these checks before starting the deployment. Do not proceed if any of them fail.

```bash
# Docker daemon is running and reachable
docker info

# Current user can talk to the Docker socket without sudo
docker ps

# Compose plugin is installed
docker compose version

# Sufficient free disk space for the image and log files
df -h .
```

Image and container size is documented in [README.md § Image Size](README.md#image-size).

If you plan to use an alert channel that requires outbound network access (Slack webhook, SMTP relay, or SNMP trap receiver), confirm the host can reach that endpoint before deployment.

> Repository layout is documented in [8.1 Repository Structure](#81-repository-structure).

---

## 2. Environment Setup

> If you plan to use the automated installer ([3.1 Option A: Automated Installation (install.sh)](#31-option-a-automated-installation-installsh-recommended), recommended), the configuration wizard creates `.env` and `watchdog-config.yaml` for you interactively — you may skip 2.2–2.4 and go directly to [3. Deployment Steps](#3-deployment-steps). Complete 2.2–2.4 only if you are installing manually ([3.2 Option B: Manual Installation](#32-option-b-manual-installation)) or want to pre-stage configuration before running the installer.

### 2.1 Clone the Repository

```bash
git clone https://github.com/Radware/CyberController-Container-Alert Container_Alert
cd Container_Alert
```

All remaining commands in this guide are run from inside the `Container_Alert/` directory.

---

### 2.2 Configure Environment Variables (.env)

```bash
cp .env.example .env
chmod 600 .env          # restrict to owner only — contains secrets
```

Open `.env` and fill in every value:

```dotenv
# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...

# SMTP (only if SMTP channel is enabled)
SMTP_USERNAME=your-smtp-username/login email
SMTP_PASSWORD=your-smtp-password/api key value

# Tuning (optional — defaults shown)
LOG_LEVEL=INFO
```

Only credentials and secrets belong in `.env`. Hosts, ports, recipients, and thresholds are configured in `watchdog-config.yaml`, never hardcoded in this guide or in scripts.

---

### 2.3 Configure Watchdog Global Settings

```yaml
# ── Watchdog global settings ────────────────────────────────────────────────────────
check_interval_seconds: 60      # poll interval
cooldown_minutes: 5             # no duplicate alerts within this window
restart_threshold: 5            # restarts before "restart-loop" alert
restart_window_minutes: 10      # rolling window for restart counting
unhealthy_cycles_threshold: 3   # consecutive unhealthy polls before alert

excluded_containers:
  - debug-shell                 # add any containers you don't want monitored
```

---

### 2.4 Configure Alert Channels

| Channel | `alert_channels` identifier | What you need |
|---|---|---|
| Slack Webhook | `slack` | Slack incoming webhook URL |
| Syslog | `syslog` | Syslog server reachable from the Docker host |
| Email (SMTP) | `smtp` | SMTP relay and credentials |
| SNMP Traps | `snmp_trap` | SNMP trap receiver (NMS / SIEM) |

At least one communication channel must be configured before the watchdog can send alerts. Choose from Slack, SMTP, SNMP Trap, or Syslog, then obtain the required credentials or endpoint details for your chosen channel(s) and configure them in `watchdog-config.yaml` and `.env`.

For each channel you want to use, follow the relevant option below. Ensure its identifier is listed under `alert_channels` in `watchdog-config.yaml`.

#### 2.4.1 Slack Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Container Watchdog`, select your workspace
3. In the left menu: **Incoming Webhooks** → toggle **On**
4. Click **Add New Webhook to Workspace** → choose an alert channel (e.g. `#container-alerts`)
5. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
6. Paste it into `.env` as `SLACK_WEBHOOK_URL`

Edit `watchdog-config.yaml` to match your environment:
```yaml
# ── Slack ─────────────────────────────────────────────────────────────────────
slack:
  enabled: true                        # set to true to enable
  webhook_url_env: SLACK_WEBHOOK_URL   # env var: https://hooks.slack.com/services/...

# ── Alert channels ────────────────────────────────────────────────────────────
# Empty for local testing — alerts are still logged to stdout/file.
alert_channels:
  - slack
```


#### 2.4.2 Syslog Forwarding

Enable the syslog block in `watchdog-config.yaml`:

```yaml
# ── Syslog (remote / central log server) ─────────────────────────────────────
syslog:
  enabled: true             # set to true to enable
  host: 155.1.1.4           # IP or hostname of your syslog server
  port: 514                 # 514 = standard syslog (UDP); 601 = reliable syslog (TCP)
  protocol: udp             # udp (fire-and-forget) or tcp (reliable)
  facility: local0          # syslog facility: local0–local7, daemon, user, etc.

# ── Alert channels ────────────────────────────────────────────────────────────
# Empty for local testing — alerts are still logged to stdout/file.
alert_channels:
  - syslog
```

---

#### 2.4.3 SMTP (Email)

Add `smtp` to `alert_channels` in `watchdog-config.yaml` and configure the block:

```yaml
# ── Email (SMTP) ─────────────────────────────────────────────────────────────
# Add "smtp" to alert_channels above to enable.
smtp:
  enabled: true                      # set to true to enable
  host: smtp.radware.com
  port: 587
  sender: noc-alerts@radware.com
  username_env: SMTP_USERNAME        # env var: SMTP username or app token (Mailtrap, Gmail app password, etc.)
  recipients:
    - ops-team@radware.com
    - oncall@radware.com
  tls: true
  password_env: SMTP_PASSWORD        # env var: SMTP password or app token

# ── Alert channels ────────────────────────────────────────────────────────────
# Empty for local testing — alerts are still logged to stdout/file.
alert_channels:
  - smtp
```

Set SMTP credentials in `.env`:

```dotenv
SMTP_USERNAME=your-smtp-username/ login email
SMTP_PASSWORD=your-smtp-password/ api key value
```

---

#### 2.4.4 SNMP Traps

Add `snmp_trap` to `alert_channels` in `watchdog-config.yaml` and configure the block:

```yaml
# ── SNMP Traps ────────────────────────────────────────────────────────────────
# Sends SNMP traps (v1, v2c, or v3) to your NMS/SIEM on every alert.
# Add "snmp_trap" to alert_channels above to enable.
# Requires: pip install pysnmp (already in requirements-watchdog.txt)
snmp_trap:
  enabled: false                     # set to true to enable
  host: 155.1.1.1                    # IP or hostname of your SNMP trap receiver (NMS)
  port: 162                          # UDP port — 162 is the standard SNMP trap port
  version: v2c                       # SNMP version: v1, v2c, or v3
  community: public                  # SNMPv1/v2c community string (ignored for v3)
  # trap_oid is not set here — code defaults to Radware enterprise OID 1.3.6.1.4.1.89.110.0.1
  # SNMPv3 settings (used only when version: v3 — add secrets to .env)
  # v3_username:      watchdog-user
  # v3_auth_protocol: SHA              # MD5 | SHA | SHA256 | SHA384 | SHA512
  # v3_auth_key_env:  SNMP_V3_AUTH_KEY # env var in .env holding the auth passphrase
  # v3_priv_protocol: AES              # AES (AES-128, recommended) | AES192 | AES256 | DES (weak — logs warning)
  # v3_priv_key_env:  SNMP_V3_PRIV_KEY # env var in .env holding the priv passphrase
  # v3_local_engine_id: ""             # Pin the sender engine ID (hex, with or without 0x).
  #   The watchdog logs the auto-derived engine ID at startup (INFO level) — copy it here.
  #   Then set in snmptrapd.conf: createUser -e 0x<id> watchdog-user SHA "..." AES "..."
  #   This prevents snmptrapd from needing reconfiguration after container restarts.

# ── Alert channels ────────────────────────────────────────────────────────────
# Empty for local testing — alerts are still logged to stdout/file.
alert_channels:
  - snmp_trap
```

> **Security note:** `public` is the well-known default SNMPv1/v2c community string and provides no real access control — anyone who can reach the trap receiver on UDP/162 with that string can be trusted implicitly. Change it to a non-default value, restrict network access to the trap receiver, or use SNMPv3 with authentication and encryption for production deployments.

Trap var-binds and SNMPv3 setup notes are documented in [8.3 SNMP Trap Var-Binds](#83-snmp-trap-var-binds).

---

## 3. Deployment Steps

### 3.1 Option A: Automated Installation (install.sh) (Recommended)

Run the installer from the `Container_Alert/` directory created in [2.1 Clone the Repository](#21-clone-the-repository). It guides you through every step interactively — including environment and alert-channel configuration — and requires no manual file editing.

> **Before running:** If internet access is available, no additional steps are required as the installer will automatically build the Docker image from source; otherwise, download the pre-built Docker image archive [watchdog.tar](https://radwareil.sharepoint.com/:f:/s/NAResidentEngineers/IgC4d3ALtPwvSrz4is6QWmnPATRCQ7g6BXbnbWO3Q0FFMSk?e=oj4WGS) and place it in the same directory as install.sh before starting the installation.
If you have trouble accessing the download link, see [8.4 Support Contacts](#84-support-contacts).

```bash
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

Continue to [4. Verification](#4-verification).

---

### 3.2 Option B: Manual Installation

Use this path when you need full control over configuration files before starting. This procedure is optional — skip it if you completed Option A. It assumes you already completed [2.1 Clone the Repository](#21-clone-the-repository) and [2.2–2.4 Environment Setup](#2-environment-setup).

#### Online Installation

*Note* - If internet access is available, build the image and skip to [Start the container](#start-the-container) below.

```bash
docker compose -f docker-compose.build.yaml build
```

#### Offline Installation

*Note* - If internet access is unavailable, load the pre-built image instead of building it.

Download [`watchdog.tar` — pre-built Docker image for offline installation](https://radwareil.sharepoint.com/:f:/s/NAResidentEngineers/IgC4d3ALtPwvSrz4is6QWmnPATRCQ7g6BXbnbWO3Q0FFMSk?e=oj4WGS). If you have trouble accessing the download link, see [8.4 Support Contacts](#84-support-contacts).
```bash
docker load -i watchdog.tar
```

#### Start the container

```bash
docker compose up -d
```

Continue to [4. Verification](#4-verification).

---

## 4. Verification

### 4.1 Confirm Container Status and Health

```bash
docker compose ps
docker compose logs -f docker-container-watchdog
```

Expected output from `docker compose ps`:

```
NAME                        STATUS
docker-container-watchdog   Up (healthy)
```

If the status does not reach `Up (healthy)` within a couple of minutes, see [7. Troubleshooting](#7-troubleshooting), or follow [5. Rollback](#5-rollback) to back out the deployment.

---

### 4.2 Reviewing Logs

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

## 5. Rollback

### 5.1 Roll Back a Failed Deployment

If verification fails immediately after `docker compose up -d` — the container will not start, restarts in a loop, or the health check never turns healthy:

```bash
docker compose down
```

Diagnose the cause using [4.2 Reviewing Logs](#42-reviewing-logs) and [7. Troubleshooting](#7-troubleshooting), correct the configuration, then redeploy:

```bash
docker compose up -d
```

Re-run [4.1 Confirm Container Status and Health](#41-confirm-container-status-and-health) before considering the deployment complete.

### 5.2 Roll Back an Image Upgrade

The runtime image is always tagged `watchdog:latest`, so loading or pulling a new image overwrites the previous one. Before upgrading the image (see [6.3 Upgrade Guide](#63-upgrade-guide)), tag the current working image so it can be restored:

```bash
docker tag watchdog:latest watchdog:rollback
```

If the new image misbehaves after `docker load -i watchdog.tar` or `docker compose pull`, restore the previous image and redeploy:

```bash
docker tag watchdog:rollback watchdog:latest
docker compose up -d
```

### 5.3 Roll Back an Application Upgrade (Git)

Before running `git pull` (see [6.3 Upgrade Guide](#63-upgrade-guide)), record the current commit so you can return to it:

```bash
git rev-parse HEAD > backup/previous-commit.txt
```

To roll back to the previous version after a failed upgrade:

```bash
git checkout "$(cat backup/previous-commit.txt)"
docker compose up -d
```

Also restore the `.env` and `watchdog-config.yaml` files backed up before the upgrade — see [6.3 Upgrade Guide](#63-upgrade-guide).

---

## 6. Operations and Maintenance

### 6.1 Lifecycle Management

#### Stopping the Service

```bash
# Pause the container — restartable with `docker compose start`
docker compose stop

# Stop and remove the container, keep the image and log files
docker compose down
```

#### Restarting After Configuration Changes

```bash
# Pick up watchdog-config.yaml changes (no rebuild needed)
docker compose restart docker-container-watchdog

# Re-read .env credential changes
docker compose up -d
```

#### Updating the Watchdog Image

```bash
# Rebuild the image (requires internet)
docker compose -f docker-compose.build.yaml build

# Redeploy using the production runtime file
docker compose up -d
```

---

### 6.2 Applying Configuration Changes

No image rebuild is required for any configuration change. The type of change determines the required command:

| Changed file | What to run |
|---|---|
| `watchdog-config.yaml` (thresholds, channels, exclusions) | `docker compose restart docker-container-watchdog` |
| `.env` (secrets, credentials) | `docker compose up -d` |
| `docker-compose.yaml` (host name, resource limits) | `docker compose up -d` |

#### Apply watchdog-config.yaml changes

```bash
docker compose restart docker-container-watchdog
```

#### Apply .env changes (credentials / secrets)

```bash
docker compose up -d
```

#### Apply docker-compose.yaml changes (container settings)

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

### 6.3 Upgrade Guide

There are two ways to upgrade the Watchdog: the application code, or the Docker image only.

#### Before You Begin (Recommended)

Back up your deployment-specific configuration files before upgrading. These backups are also used for [5. Rollback](#5-rollback) if the upgrade fails.

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

#### Upgrade Application Only

##### Option A: Upgrade from Git Repository

*Note* - Requires internet connectivity.

1. Navigate to the project directory:

   ```bash
   cd Container_Alert
   ```

2. Record the current commit, for [rollback](#53-roll-back-an-application-upgrade-git) if needed:

   ```bash
   mkdir -p backup
   git rev-parse HEAD > backup/previous-commit.txt
   ```

3. Stop the running container:

   ```bash
   docker compose down
   ```

4. Download the latest source code:

   ```bash
   git fetch origin
   git pull origin main
   ```

5. Start the updated container:

   ```bash
   docker compose up -d
   ```

6. Verify:

   ```bash
   docker ps
   docker compose logs docker-container-watchdog
   ```

   If verification fails, see [5.3 Roll Back an Application Upgrade (Git)](#53-roll-back-an-application-upgrade-git).

---

##### Option B: Upgrade Application Only (Offline)

1. Obtain the latest version of the application. You can either obtain the latest archive from the RE team or download it directly from GitHub (https://github.com/Radware/CyberController-Container-Alert.git).
2. Uninstall the current version — see [6.4 Uninstalling](#64-uninstalling).
3. Replace the existing files in the application directory:

   ```bash
   cd /path/to/Container_Alert
   ```

4. Redeploy the latest version — see [3. Deployment Steps](#3-deployment-steps).

---

#### Upgrade Docker Image Only

Automatic upgrades are **not enabled** by default. Before upgrading, tag the current image so it can be [rolled back](#52-roll-back-an-image-upgrade) if the new image misbehaves:

```bash
docker tag watchdog:latest watchdog:rollback
```

There are two ways to upgrade the image — online or offline. Follow only one of the options.

##### Option A: Online Image Upgrade

If you have internet access, pull the latest image:

```bash
docker compose pull
docker compose up -d
```

##### Option B: Offline Image Upgrade

1. Request the Radware RE team to create an updated pre-built Docker image for offline installation. See [8.4 Support Contacts](#84-support-contacts).
2. Load the image. Make sure the new image uses the same name, `watchdog:latest`.

   ```bash
   docker load -i watchdog.tar
   docker compose up -d
   ```

---

#### Checking the Installed Version

View the image used by the running container:

```bash
docker inspect docker-container-watchdog --format='{{.Config.Image}}'
```

View locally available Watchdog images:

```bash
docker images watchdog
```

---

### 6.4 Uninstalling

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

#### Fully non-interactive

```bash
bash uninstall.sh --remove-all --force
```

Skips all confirmation prompts. Use in automated pipelines or when scripting repeated clean installs.

> `--keep-logs` and `--remove-all` are mutually exclusive and cannot be combined.

---

## 7. Troubleshooting

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

If none of the above resolves the issue, see [5. Rollback](#5-rollback) to back out a recent change and redeploy from a known-good state.

---

## 8. Appendix and Reference

### 8.1 Repository Structure

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

### 8.2 Configuration Reference

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
| `excluded_containers` | `[]` | Containers that are never alerted on |
| `log_level` | `INFO` | Logging verbosity: `INFO` (recommended for production) or `DEBUG` |

#### Host Identification in Alerts

Set `WATCHDOG_HOST` in `docker-compose.yaml` under the watchdog service environment:

```yaml
environment:
  WATCHDOG_HOST: my-server-name
```

---

### 8.3 SNMP Trap Var-Binds

Configuration for the SNMP Traps channel is in [2.4.4 SNMP Traps](#244-snmp-traps).

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

### 8.4 Support Contacts

If you have trouble accessing the `watchdog.tar` download link, or need an updated offline image from the Radware RE team, contact:

- rahulku@radware.com
- Egore@radware.com
- northamericare@radware.com
