# CyberController Container Watchdog — Deployment Guide

The **CyberController Container Watchdog** is an autonomous monitoring service that provides continuous health visibility across all Docker containers running on the host. It detects failures — including crashes, out-of-memory kills, prolonged unhealthy states, and restart loops — and dispatches real-time alerts through one or more configurable channels (**Slack**, **SMTP**, **SNMP Traps**, or **Syslog**).

This guide covers initial deployment, alert channel configuration, and ongoing operational management of the watchdog service.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Structure](#2-repository-structure)
3. [Install and Deploy](#3-install-and-deploy)
4. [Logs](#4-logs)
5. [Configuration Reference](#5-configuration-reference)
6. [Lifecycle Management](#6-lifecycle-management)
7. [How to Change Existing Settings](#7-how-to-change-existing-settings)
8. [Uninstalling](#8-uninstalling)
9. [Upgrade Guide](#9-upgrade-guide)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum Version |
|---|---|
| Docker Engine | 20.10+ |
| Docker Compose plugin **or** standalone | v2 plugin (`docker compose`)
| Linux host (for Docker socket) | Ubuntu 20.04 / RHEL 8 or later |


```bash
docker compose version  
```
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

#### 1. Clone the repository 
```bash
cd /path/to/Container_Alert
git clone https://github.com/Radware/CyberController-Container-Alert
```
#### 2. Run the installer from the `Container_Alert/` directory. It guides you through every step interactively and requires no manual file editing.

> **Before running:** If internet access is available, no additional steps are required as the installer will automatically build the Docker image from source; otherwise, download the pre-built Docker image archive [watchdog.tar](https://radwareil.sharepoint.com/:f:/s/NAResidentEngineers/IgC4d3ALtPwvSrz4is6QWmnPATRCQ7g6BXbnbWO3Q0FFMSk?e=oj4WGS) and place it in the same directory as install.sh before starting the installation.

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

---

### Option B — Manual installation

Use this path when you need full control over configuration files before starting.
This procedure (Option B) is optional, skip this if you completed option A.

#### Online Installation

*Note* - If internet access is available proceed to step 1 and 2(skip steps 3 and 4).

#### 1. Clone the repository 
```bash
cd /path/to/Container_Alert
git clone https://github.com/Radware/CyberController-Container-Alert
```
#### 2. Build the image (requires internet)
```bash
docker compose -f docker-compose.build.yaml build
```
#### Offline Installation

*Note* - If internet access is unavailable proceed to step 3 and 4(skip steps 1 and 2).

#### 3. Manually Download the repository and upload to your local path.

Download repository: https://github.com/Radware/CyberController-Container-Alert
Upload to: /path/to/Container_Alert

#### 4. Load the Docker image

Download [`watchdog.tar`- pre-built Docker image for offline installation](https://radwareil.sharepoint.com/:f:/s/NAResidentEngineers/IgC4d3ALtPwvSrz4is6QWmnPATRCQ7g6BXbnbWO3Q0FFMSk?e=oj4WGS):

> **Note:** If you have trouble accessing the download link, contact:
> - rahulku@radware.com
> - Egore@radware.com
> - northamericare@radware.com

```bash
docker load -i watchdog.tar
```
#### 5. Configure Environment Variables

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
---

#### 6. Configure the Alert Channel/s

| Channel | `alert_channels` identifier | What you need |
|---|---|---|
| Slack Webhook | `slack` | Slack incoming webhook URL |
| Syslog | `syslog` | Syslog server reachable from the Docker host |
| Email (SMTP) | `smtp` | SMTP relay and credentials |
| SNMP Traps | `snmp_trap` | SNMP trap receiver (NMS / SIEM) |

 At least one communication channel must be configured before the watchdog can send alerts. Choose from Slack, SMTP, SNMP Trap, or Syslog, then obtain the required credentials or endpoint details for your chosen channel(s) and configure them in `watchdog-config.yaml` and `.env`.

For each channel you want to use, follow the relevant option below. Ensure its identifier is listed under `alert_channels` in `watchdog-config.yaml`.

#### Configure the Watchdog global settings

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
### Configuration File (watchdog-config.yaml)



### Option A — Slack Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Container Watchdog`, select your workspace
3. In the left menu: **Incoming Webhooks** → toggle **On**
4. Click **Add New Webhook to Workspace** → choose an alert channel (e.g. `#container-alerts`)
5. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
6. Paste it into `.env` as `SLACK_WEBHOOK_URL`

Edit `watchdog-config.yaml` to match your environment:
```yaml
# ── Alert channels ────────────────────────────────────────────────────────────
# Empty for local testing — alerts are still logged to stdout/file.
alert_channels:
  - slack

# ── Slack ─────────────────────────────────────────────────────────────────────
slack:
  enabled: true                        # set to true to enable
  webhook_url_env: SLACK_WEBHOOK_URL   # env var: https://hooks.slack.com/services/...

```
---


### Option B — Syslog Forwarding

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

### Option C — SMTP (Email)

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

### Option D — SNMP Traps

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

#### 7. Start the container

```bash
docker compose up -d
```

#### 8. Verify

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


## 4. Logs

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

## 5. Configuration Reference

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

## 6. Lifecycle Management

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

## 7. How to Change Existing Settings

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

## 8. Uninstalling

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

## 9. Upgrade Guide

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

### Upgrade application only 

#### Option A - Upgrade from Git Repository

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


```bash
git fetch origin
git pull origin main
```

#### 4. Start the updated container

```bash
docker compose up -d
```

#### 5. Verify

```bash
docker ps
docker compose logs docker-container-watchdog
```

---

### Option B - Upgrade application only (offline)

#### 1. Obtain the latest version of application 
You can either obtain the latest archive from the RE team or download it directly from GitHub (https://github.com/Radware/CyberController-Container-Alert.git).

#### 2. Uninstall the current version
Please refer to [Uninstalling](#11-uninstalling) section.

#### 3. Replace the existing files in the application directory
cd /path/to/Container_Alert

#### 4. Redeploy the latest version
Please refer to [Install and Deploy](#3-install-and-deploy) [Install and Deploy](#3-install-and-deploy) section.

---

### Upgrade Docker Image Only

Automatic upgrades are **not enabled** by default.

*Note* - There are two ways to upgarde the image - Online or Offline. Follow only one of the options.

#### Option A - Online Image Upgrade

If you have internet access, you can pull the latest image:

```bash
docker compose pull
docker compose up -d
```
#### Option B - Offline Image Upgrade

#### 1. Request Radware RE team to create an updated version of pre-built Docker image for offline installation:

 Please contact:
 - rahulku@radware.com
 - Egore@radware.com
 - northamericare@radware.com
 
 #### 2. Load the image
 *Note* - Please make sure the new image have same name watchdog:latest.

```bash
docker load -i watchdog.tar
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

## 10. Troubleshooting

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
