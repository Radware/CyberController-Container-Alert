# CyberController Container Watchdog — Deployment Guide

The watchdog monitors all Docker containers on the host and fires alerts via one or more configured channels (**Slack**, **Splunk HEC**, **SMTP**, **SNMP**, or **Syslog**) whenever a container crashes, is OOM-killed, becomes unhealthy, or enters a restart loop.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Structure](#2-repository-structure)
3. [Step 1 — Configure Environment Variables](#3-step-1--configure-environment-variables)
4. [Step 2 — Configure the Watchdog](#4-step-2--configure-the-watchdog)
5. [Step 3 — Configure Alert Channels](#5-step-3--configure-alert-channels)
6. [Step 4 — Install and Deploy](#6-step-4--install-and-deploy)
7. [Step 5 — Verify It Is Working](#7-step-5--verify-it-is-working)
8. [Logs](#8-logs)
9. [Alert Types](#9-alert-types)
10. [Tuning](#10-tuning)
11. [Stopping, Updating and Uninstalling](#11-stopping-updating-and-uninstalling)
12. [Troubleshooting](#12-troubleshooting)

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
| Slack workspace *(if using Slack alerts)* | — |
| Splunk instance with HEC enabled *(if using Splunk HEC)* | Splunk 8.x or later |

> **Windows / macOS:** The watchdog mounts `/var/run/docker.sock` and is designed to run on a Linux Docker host. For local development on Windows, use Docker Desktop with WSL 2.

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

## 3. Step 1 — Configure Environment Variables

```bash
cp .env.example .env
chmod 600 .env          # restrict to owner only — contains secrets
```

Open `.env` and fill in every value:

```dotenv
# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...

# Splunk HEC
SPLUNK_HEC_URL=https://<your-splunk-host>:8088
SPLUNK_HEC_TOKEN=<your-hec-token>

# Tuning (optional — defaults shown)
LOG_LEVEL=INFO
```
---

## 4. Step 2 — Configure the Watchdog

Edit `watchdog-config.yaml` to match your environment:

```yaml
alert_channels:          # choose one or more: slack, splunk_hec, smtp, snmp_trap
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

splunk_hec:
  hec_url_env:   SPLUNK_HEC_URL        # reads from .env
  hec_token_env: SPLUNK_HEC_TOKEN      # reads from .env
  alert_index:   main                  # Splunk index for container alerts
  log_index:     main                  # Splunk index for watchdog logs
  log_enabled:   false                 # true = also stream watchdog logs to Splunk
  verify_ssl:    true                  # false only for self-signed certs
```

---

## 5. Step 3 — Configure Alert Channels

All alert channels are **optional** — configure at least one so the watchdog has somewhere to send alerts. Multiple channels can be active simultaneously; add each identifier to `alert_channels` in `watchdog-config.yaml`.

| Channel | `alert_channels` identifier | What you need |
|---|---|---|
| Slack Webhook | `slack` | Slack workspace with an incoming webhook |
| Splunk HEC | `splunk_hec` | Splunk 8.x+ with HTTP Event Collector enabled |
| Syslog | *(syslog config block)* | Syslog server reachable from the Docker host |
| Email (SMTP) | `smtp` | SMTP relay and credentials |
| SNMP Traps | `snmp_trap` | SNMP trap receiver (NMS / SIEM) |

Choose one or more options below and complete only the relevant steps.

---

### Option A — Slack Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Container Watchdog`, select your workspace
3. In the left menu: **Incoming Webhooks** → toggle **On**
4. Click **Add New Webhook to Workspace** → choose an alert channel (e.g. `#container-alerts`)
5. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
6. Paste it into `.env` as `SLACK_WEBHOOK_URL`

---

### Option B — Splunk HEC

#### Enable HEC in Splunk

1. In Splunk Web: **Settings → Data Inputs → HTTP Event Collector**
2. Click **Global Settings** → set **All Tokens** to **Enabled** → Save
3. Click **New Token**:
   - **Name:** `container-watchdog`
   - **Source type:** `container:alert`
   - **Index:** select or create `main` (or a dedicated index like `containers`)
4. Complete the wizard and copy the **Token Value**
5. Note your Splunk server URL, e.g. `https://splunk.radware.internal:8088`
6. Paste both into `.env`:
   ```dotenv
   SPLUNK_HEC_URL=https://splunk.radware.internal:8088
   SPLUNK_HEC_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

#### Verify HEC is reachable

```bash
curl -k https://<splunk-host>:8088/services/collector/health
# Expected: {"text":"HEC is healthy","code":17}
```

> If your Splunk uses a self-signed certificate, set `verify_ssl: false` in `watchdog-config.yaml`.

---

### Option C — Syslog Forwarding

To forward watchdog logs to a central syslog server (rsyslog, syslog-ng, Graylog), enable the syslog block in `watchdog-config.yaml`:

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

## 6. Step 4 — Install and Deploy

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

Edit `watchdog-config.yaml` — set `alert_channels`, thresholds, and any channel-specific blocks (see [Step 3](#5-step-3--configure-alert-channels)).

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

### Option C — Build offline image and deploy to air-gapped host

Use this when the target host has no internet access. Build the image on a machine that does, export it as a tar archive, transfer it, then deploy.

#### Step 1 — Build the image (on an internet-connected machine)

```bash
# Clone or copy the source files, then run from the Container_Alert/ directory:
docker build -t watchdog:latest .
```

#### Step 2 — Export the image to a tar file

```bash
docker save watchdog:latest -o watchdog.tar
```

Verify the file was created:

```bash
ls -lh watchdog.tar
# Expected: around 140 MB
```

#### Step 3 — Transfer the tar to the target host

```bash
scp watchdog.tar root@<target-host>:/opt/radware/storage/scripts/Alert_container/
```

Or copy via USB / shared drive if SCP is not available.

#### Step 4 — Load the image on the target host

```bash
docker load -i watchdog.tar
```

Verify the image loaded:

```bash
docker images watchdog
# Expected: watchdog   latest   <id>   <size>
```

#### Step 5 — Start the container

```bash
# v1 standalone
docker-compose up -d

# v2 plugin
docker compose up -d
```

#### Step 6 — Verify

```bash
docker ps --filter name=watchdog
docker-compose logs -f watchdog
```

---

## 7. Step 5 — Verify It Is Working

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

> **Splunk HEC:** Search `index=main sourcetype="container:alert" | table _time, severity, container_name, failure_type, host` to confirm events are arriving.

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

## 10. Tuning

All settings are in `watchdog-config.yaml`. No rebuild is required — restart the watchdog container to pick up config changes:

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
| `excluded_containers` | `[debug-shell, load-test]` | Containers never alerted on |
| `log_level` | `DEBUG` | `INFO` or `DEBUG` — set to `INFO` in production |

### Identify this host in alerts

Set `WATCHDOG_HOST` in `docker-compose.yaml` under the watchdog service environment:

```yaml
environment:
  WATCHDOG_HOST: my-server-name
```

---

## 11. Stopping, Updating and Uninstalling

### Stopping the watchdog (without removing)

```bash
# Pause the container — restartable with `docker compose start`
docker compose stop

# Stop and remove the container, keep the image and log files
docker compose down
```

### Restarting after config changes

```bash
# Pick up watchdog-config.yaml changes (no rebuild needed)
docker compose restart watchdog

# Re-read .env credential changes
docker compose up -d
```

### Updating the watchdog

```bash
# Rebuild the image (requires internet)
docker build -t watchdog:latest .

# Redeploy
docker-compose up -d        # v1
docker compose up -d        # v2
```

---

### Uninstalling with `uninstall.sh`

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

## 12. Troubleshooting

### Watchdog container is not starting

```bash
docker compose logs watchdog
```

Common causes:
- `/var/run/docker.sock` is not accessible — ensure the host socket exists and the container has read access
- Missing `.env` file — run `cp .env.example .env` and fill in values

### No Slack alerts received

1. Verify `SLACK_WEBHOOK_URL` is set correctly in `.env`
2. Test the webhook manually:
   ```bash
   curl -X POST -H 'Content-type: application/json' \
     --data '{"text":"Watchdog test"}' \
     "$SLACK_WEBHOOK_URL"
   ```
   Expected response: `ok`

### No Splunk events appearing

1. Verify HEC is enabled in Splunk (Settings → Data Inputs → HTTP Event Collector → Global Settings)
2. Test the HEC endpoint:
   ```bash
   curl -k https://<splunk-host>:8088/services/collector/health
   ```
3. Check the token is not disabled in Splunk
4. If using HTTPS with self-signed cert, set `verify_ssl: false` in `watchdog-config.yaml`
5. Confirm the index exists in Splunk — create `main` if it does not exist

### Duplicate alerts

Increase `cooldown_minutes` in `watchdog-config.yaml`. The default is 5 minutes per container per failure type.

### Too many alerts / noise

Add noisy containers to `excluded_containers` in `watchdog-config.yaml`:

```yaml
excluded_containers:
  - debug-shell
  - load-test
  - my-noisy-container
```
