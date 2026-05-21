# CyberController Container Watchdog — Deployment Guide

The watchdog monitors all Docker containers on the host and fires alerts to **Slack** and **Splunk** whenever a container crashes, is OOM-killed, becomes unhealthy, or enters a restart loop.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Structure](#2-repository-structure)
3. [Step 1 — Configure Environment Variables](#3-step-1--configure-environment-variables)
4. [Step 2 — Configure the Watchdog](#4-step-2--configure-the-watchdog)
5. [Step 3 — Set Up Slack Webhook](#5-step-3--set-up-slack-webhook)
6. [Step 4 — Set Up Splunk HEC](#6-step-4--set-up-splunk-hec)
7. [Step 5 — Build and Deploy](#7-step-5--build-and-deploy)
8. [Step 6 — Verify It Is Working](#8-step-6--verify-it-is-working)
9. [Logs](#9-logs)
10. [Alert Types](#10-alert-types)
11. [Tuning](#11-tuning)
12. [Stopping and Updating](#12-stopping-and-updating)
13. [Optional — Syslog Forwarding](#13-optional--syslog-forwarding)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum Version |
|---|---|
| Docker Engine | 24.x |
| Docker Compose plugin | v2.x (`docker compose`) |
| Linux host (for Docker socket) | Ubuntu 20.04 / RHEL 8 or later |
| Slack workspace with admin access | — |
| Splunk instance with HEC enabled | Splunk 8.x or later |

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

> **Never commit `.env` to version control.** Add it to `.gitignore`.

---

## 4. Step 2 — Configure the Watchdog

Edit `watchdog-config.yaml` to match your environment:

```yaml
alert_channels:
  - slack
  - splunk

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

splunk:
  hec_url_env:   SPLUNK_HEC_URL        # reads from .env
  hec_token_env: SPLUNK_HEC_TOKEN      # reads from .env
  alert_index:   main                  # Splunk index for container alerts
  log_index:     main                  # Splunk index for watchdog logs
  log_enabled:   false                 # true = also stream watchdog logs to Splunk
  verify_ssl:    true                  # false only for self-signed certs
```

---

## 5. Step 3 — Set Up Slack Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Container Watchdog`, select your workspace
3. In the left menu: **Incoming Webhooks** → toggle **On**
4. Click **Add New Webhook to Workspace** → choose an alert channel (e.g. `#container-alerts`)
5. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
6. Paste it into `.env` as `SLACK_WEBHOOK_URL`

---

## 6. Step 4 — Set Up Splunk HEC

### Enable HEC in Splunk

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

### Verify HEC is reachable

```bash
curl -k https://<splunk-host>:8088/services/collector/health
# Expected: {"text":"HEC is healthy","code":17}
```

> If your Splunk uses a self-signed certificate, set `verify_ssl: false` in `watchdog-config.yaml`.

---

## 7. Step 5 — Build and Deploy

### First-time deployment (with internet / build machine)

```bash
# From the Container_Alert/ directory
# Build the image and start the watchdog
docker compose -f docker-compose.build.yaml up -d
```

### First-time deployment (offline / pre-built image)

```bash
# Load the exported image, then start
docker load -i watchdog.tar
docker compose up -d
```

This will:
- Start the watchdog container using the pre-built `watchdog:latest` image

### Check all containers started

```bash
docker compose ps
```

Expected output:

```
NAME       STATUS
watchdog   Up (healthy)
```

---

## 8. Step 6 — Verify It Is Working

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

Within 60 seconds (or immediately via the event listener) you should receive:
- A Slack message in your configured channel
- A Splunk event in the `main` index (search: `sourcetype="container:alert"`)

### Splunk search to confirm

```
index=main sourcetype="container:alert"
| table _time, severity, container_name, failure_type, host
```

---

## 9. Logs

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

## 10. Alert Types

| Failure Type | Severity | Trigger |
|---|---|---|
| `crashed` | CRITICAL | Container exited with non-zero exit code |
| `oom` | CRITICAL | Container was OOM-killed by the kernel |
| `unhealthy` | HIGH | Health probe failed for `unhealthy_cycles_threshold` consecutive cycles |
| `restart-loop` | HIGH | Container restarted ≥ `restart_threshold` times within `restart_window_minutes` |

Each alert includes: container name, container ID, host, failure type, timestamp, exit code (if applicable), recommended action, runbook URL, and the last 20 log lines.

---

## 11. Tuning

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

## 12. Stopping and Updating

### Stop all services

```bash
docker compose down
```

### Stop without removing volumes

```bash
docker compose stop
```

### Update after code changes

```bash
# Rebuild the image, then redeploy
docker compose -f docker-compose.build.yaml build
docker compose up -d
```

### Update only the watchdog

```bash
docker compose -f docker-compose.build.yaml build watchdog
docker compose up -d watchdog
```

---

## 13. Optional — Syslog Forwarding

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

## 14. Troubleshooting

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
