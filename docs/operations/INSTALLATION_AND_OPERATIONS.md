# Container Alert — Customer Installation and Operations

This guide covers **installation, configuration, day-to-day operations, upgrades, rollback, and troubleshooting**.

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
   - [Get and verify the release package](#21-get-and-verify-the-release-package)
   - [Run the installer](#22-run-the-installer)
3. [Verify the installation](#3-verify-the-installation)
4. [Configuration reference](#4-configuration-reference)
   - [Configuration files and editing](#41-configuration-files-and-editing)
   - [Watchdog monitoring settings](#42-watchdog-monitoring-settings)
   - [Notification channels](#43-notification-channels)
     - [Syslog](#syslog)
     - [SMTP (email)](#smtp-email)
     - [Slack](#slack)
     - [SNMP traps](#snmp-traps)
   - [Health checks, exclusions, and HA](#44-health-checks-exclusions-and-ha)
   - [Environment variables](#45-environment-variables)
   - [Apply configuration changes](#46-apply-configuration-changes)
5. [Operations and maintenance](#5-operations-and-maintenance)
   - [Common commands and logs](#51-common-commands-and-logs)
   - [Backup and restore](#52-backup-and-restore)
   - [Upgrade the watchdog](#53-upgrade-the-watchdog)
   - [Rollback](#54-rollback)
   - [Uninstall](#55-uninstall)
6. [Troubleshooting](#6-troubleshooting)
7. [Related documentation](#7-related-documentation)

## 1. Prerequisites

| Requirement | What is needed |
|---|---|
| Operating system | Supported Linux host with Docker (Ubuntu 20.04 / RHEL 8 or later are the documented baseline) |
| Docker and Compose | Docker Engine 20.10+ and Docker Compose (plugin or standalone) |
| Host permissions | Root or an operator with Docker access; `sudo` may be required to secure configuration files |
| Release package | Published `container-alert-v<VERSION>.tar.gz` and matching `SHA256SUMS` |
| Network access | Optional; download from [GitHub Releases](https://github.com/Radware/CyberController-Container-Alert/releases) or transfer both files from another machine |
| Alert delivery | Network connectivity to the chosen Syslog, SMTP, SNMP, or Slack destination |


## 2. Installation

Download the installation package directly, or transfer the same package to a CyberController host `opt/radware/storage/scripts/` directory. The release package includes the installer, prebuilt Docker image and configuration templates.

The examples use v1.6.0; download a version from [GitHub Releases](https://github.com/Radware/CyberController-Container-Alert/releases).

### 2.1 Get and verify the release package

**Step 1 — Choose a release version.** Open the [Container Alert Releases page](https://github.com/Radware/CyberController-Container-Alert/releases) to see all published versions. The release marked **Latest** is usually the newest stable version.

**Step 2 — Obtain the release package and checksum.** Use either method to get the **same two files** on the target host:

**Host has Internet access:** From a root shell (or account with write access), download into the scripts directory:

```bash
mkdir -p /opt/radware/storage/scripts
cd /opt/radware/storage/scripts
VER=1.6.0  # Example: GitHub tag v1.6.0
URL="https://github.com/Radware/CyberController-Container-Alert/releases/download/v${VER}"
curl -fL -O "$URL/container-alert-v${VER}.tar.gz" -O "$URL/SHA256SUMS"
```

`curl` needs HTTPS access to GitHub Releases and its redirected download host.

**Host without Internet access:** Download the chosen release package and `SHA256SUMS` on another computer. Transfer both files to `/opt/radware/storage/scripts/` on the CyberController host.

**Step 3 — Verify and extract** the two files from the same directory:

```bash
mkdir -p /opt/radware/storage/scripts
cd /opt/radware/storage/scripts
VER=1.6.0  # Use the version chosen in Step 1
sha256sum --check SHA256SUMS
tar -xzf "container-alert-v${VER}.tar.gz"
cd /opt/radware/storage/scripts/CyberController-Container-Alert
```

The extracted directory contains `VERSION`, `install.sh`, `docker-compose.yaml`, `watchdog.tar` and its own image checksum.

### 2.2 Run the installer

**Step 4 — Start the configuration wizard:**

```bash
bash install.sh
```

The installer automatically verifies the included Docker image checksum and version, loads the image, then asks you to select and configure the notification channels (Syslog, SMTP, SNMP and/or Slack) and alert hostname. On an upgrade, answer **No** to “Reconfigure from scratch?” to preserve existing channel settings. It recreates **only the watchdog**, not other application containers.

## 3. Verify the installation

Run these commands after installation:

```bash
docker compose ps
docker inspect docker-container-watchdog --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
docker inspect docker-container-watchdog --format '{{.Config.Image}}'
```

The watchdog should be `running` and become `healthy`; the image should be `watchdog:<VERSION>` matching the package.

**Logs and alert delivery**

| Location or command | Purpose |
|---|---|
| `docker logs -f docker-container-watchdog` | Live container output |
| `docker compose logs --tail 100 docker-container-watchdog` | Recent Compose logs |
| `./watchdog/watchdog.log` | Persistent host log (rotates at 10 MB, five backups) |
| `./watchdog/state.json` | Incident, HA and reboot state; do not edit manually |
| `grep 'watchdog_alert' watchdog/watchdog.log` | Find existing alert records |

Check that the selected Syslog, SMTP, SNMP or Slack receiver can be reached and inspect any alerts already received. No artificial container failure is needed.

**Optional HA check:** If `ha_monitor.enabled: true`, check the local exporter on **each HA node**:

```bash
curl -fsS http://127.0.0.1:9664/metrics
```

Use the configured exporter URL if different. The first successful reading establishes a baseline; it does not send a failover alert. Test a real switchover only during planned HA maintenance.


## 4. Configuration reference

The installer creates the active configuration files. Edit them only when needed; most installations require no manual changes.

### 4.1 Configuration files and editing

| File | What it contains | Apply changes |
|---|---|---|
| `watchdog-config.yaml` | Monitoring, alerts, health checks and HA | Restart watchdog |
| `.env` | Credentials, hostname and Docker image version | Recreate watchdog |
| `docker-compose.yaml` | Container runtime settings; generally managed by Radware | Recreate watchdog |
| `*.example` | Reference templates, not active settings | No effect on the running service |

The installer creates `watchdog-config.yaml` and `.env`. From the installation directory, you can edit `watchdog-config.yaml` or `.env`. Back up first ([Section 5.2](#52-backup-and-restore))

### 4.2 Watchdog monitoring settings

These settings are in `watchdog-config.yaml`. Defaults shown are the application defaults; the installer may set channel-specific values.

| Setting | Default | Purpose |
|---|---|---|
| `alert_channels` | Installer selection (code default: `[slack]`) | Lists Slack, SMTP and SNMP notification channels |
| `check_interval_seconds` | `60` | Poll interval in seconds |
| `cooldown_minutes` | `5` | Reduce repeat alerts for the same incident |
| `restart_threshold` | `5` | Restarts needed to report a restart loop |
| `restart_window_minutes` | `10` | Time window for counting restarts |
| `restart_recovery_stability_seconds` | `120` | Stable running time before crash/OOM/restart-loop recovery |
| `unhealthy_cycles_threshold` | `3` | Consecutive failing health checks before alert |
| `alert_on_recovery` | `true` | Notify when an alerted condition clears |
| `lifecycle_detection.enabled` | `true` | Recognize intentional Docker stops |
| `lifecycle_detection.correlation_window_seconds` | `3` | Match stop and exit events (0.5–30 seconds) |
| `boot_grace_seconds` | `180` | Delay normal alerts after reboot; `0` disables the grace period |
| `log_level` | `INFO` | Local verbosity: DEBUG, INFO, WARNING or ERROR; forwarded Syslog priorities are separate |
| `log_file` | `/var/log/watchdog/watchdog.log` | Persistent log, mounted under `./watchdog/`; `null` disables file logging |
| `state_file` | `/var/log/watchdog/state.json` | Preserves incidents across restarts; empty value disables persistence |
| `runbook_base_url` | Empty | Optional runbook link in alerts |

Changing thresholds, exclusions or probe types can hide failures. Check their impact carefully; do not change them just to suppress alerts.

### 4.3 Notification channels

Choose channels in the installer. For later changes, edit the following YAML settings. To enable SMTP, Slack or SNMP manually, add `smtp`, `slack` or `snmp_trap` to the `alert_channels` list and enable the corresponding channel. Syslog is enabled separately with `syslog.enabled`.

| Channel | Settings in `watchdog-config.yaml` | Credentials in `.env` |
|---|---|---|
| **Syslog** | `syslog.enabled`, `syslog.host`, `syslog.port`, `syslog.protocol` (UDP/TCP), `syslog.facility` | None |
| **SMTP** | `smtp.enabled`, `smtp.auth`, `smtp.host`, `smtp.port`, `smtp.sender`, `smtp.recipients`, `smtp.tls`, `smtp.username_env` / `smtp.password_env` (**variable names only**) | `SMTP_USERNAME`, `SMTP_PASSWORD` if authentication is enabled |
| **Slack** | `slack.enabled`, `slack.webhook_url_env` | `SLACK_WEBHOOK_URL` |
| **SNMP traps** | `snmp_trap.enabled`, `snmp_trap.host`, `snmp_trap.port`, `snmp_trap.version`, `snmp_trap.community` (v1/v2c), optional `snmp_trap.trap_oid` | `SNMP_V3_AUTH_KEY`, `SNMP_V3_PRIV_KEY` for SNMPv3 |

#### Syslog

Set `syslog.enabled: true` and specify the receiver. Syslog forwards structured alerts, including INFO recoveries, regardless of local `log_level`.

#### SMTP (email)

Enable SMTP in `alert_channels` and set `smtp.enabled: true`. Set host, port, sender and recipients in YAML. `smtp.tls: true` uses STARTTLS; `smtp.auth: false` is for mail relays that do not require login.

**Important:** `username_env` and `password_env` contain **environment variable names**, not your username or password. Example:

```yaml
# watchdog-config.yaml — references only
smtp:
  username_env: SMTP_USERNAME
  password_env: SMTP_PASSWORD
```

```dotenv
# .env — actual values, never committed to Git
SMTP_USERNAME=your-mail-login
SMTP_PASSWORD=your-mail-password
```

The installer normally writes these settings for you.

#### Slack

Include `slack` in `alert_channels` and set `slack.enabled: true`. In YAML, `slack.webhook_url_env: SLACK_WEBHOOK_URL` names the variable; the **actual** webhook URL belongs in `.env`.

#### SNMP traps

Include `snmp_trap` in `alert_channels` and set `snmp_trap.enabled: true`. Enter the NMS host and UDP trap port (usually 162); versions v1/v2c use `community`.

For **SNMPv3**, configure `v3_username`, `v3_auth_protocol` (MD5, SHA, SHA256, SHA384 or SHA512) and, for encrypted traps, `v3_priv_protocol` (AES, AES192 or AES256). The `v3_auth_key_env` and `v3_priv_key_env` fields reference passphrases stored in `.env`, not plaintext in YAML. Passphrases must be at least eight characters; encryption requires authentication. DES is weak and not recommended.

Register the sender engine ID from the watchdog startup logs (`SNMP engine ID: 0x...`) with your NMS. Use `v3_local_engine_id` when a fixed sender identity is needed.

The default notification OID is `1.3.6.1.4.1.89.110.0.1`. NMS integrations receive five fields:

| Var-bind | OID | Value |
|---|---|---|
| `cwHost` | `1.3.6.1.4.1.89.110.1.1.0` | Hostname |
| `cwSummary` | `1.3.6.1.4.1.89.110.1.2.0` | Alert summary |
| `cwContainerName` | `1.3.6.1.4.1.89.110.1.3.0` | Container or HA cluster |
| `cwFailureType` | `1.3.6.1.4.1.89.110.1.4.0` | Failure type, recovery or `ha-failover` |
| `cwProbeDetail` | `1.3.6.1.4.1.89.110.1.5.0` | Detection details |

HA traps use the same OID and var-binds with `cwFailureType=ha-failover` and `cwContainerName=(HA cluster)`. Check receiver-side SNMP user/engine-ID configuration if traps are rejected. Do not trigger application failures just to test notifications.

### 4.4 Health checks, exclusions, and HA

| Setting | Default | Purpose |
|---|---|---|
| `auto_health_check.enabled` | `true` | Probe containers without a Docker health check |
| `auto_health_check.paths` | `/-/healthy`, `/health`, `/healthz`, `/api/health`, `/metrics`, `/` | Discovery paths |
| `auto_health_check.timeout_seconds` | `2` | Probe timeout |
| `container_health_checks` | `{}` | Per-container `http` / `exec` probe overrides |
| `excluded_containers` | `[]` | Containers excluded from alerts |
| `ignored_container_events` | Radware-labelled temporary check | Narrow exceptions for known transient operations |
| `ha_monitor.enabled` | `false` | Optional active-node change alerts |
| `ha_monitor.exporter_url` | `http://127.0.0.1:9664/metrics` | Local HA exporter address |

A custom probe can override discovery for a specific container. Example:

```yaml
container_health_checks:
  example-api:
    type: http
    internal_port: 8080
    path: /health
    timeout_seconds: 5
```

Fields are `type` (`http` or `exec`), `internal_port` and `path` for HTTP, `command` for exec, and optional `timeout_seconds` (default 5). Exec probes run commands **inside monitored containers**. Check the impact of probe, exclusion and ignore-rule changes before applying them.

For HA alerts, enable `ha_monitor` on both nodes with a working local exporter. Check the configured exporter URL read-only (normally `http://127.0.0.1:9664/metrics`). The first valid reading sets a baseline without sending an alert.

### 4.5 Environment variables

The installer manages most `.env` values. SMTP passwords, Slack webhooks and SNMPv3 passphrases belong here, not in YAML. The SNMPv1/v2c community is configured in YAML, so protect that file too.

| Variable | Purpose |
|---|---|
| `WATCHDOG_HOST` | Hostname shown in alerts |
| `WATCHDOG_VERSION` | Image version number only (for example `1.6.0`, without `watchdog:`) |
| `WATCHDOG_UID`, `WATCHDOG_GID`, `DOCKER_GID` | Runtime user and Docker socket permissions; installer-managed |
| `SLACK_WEBHOOK_URL` | Slack webhook |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP credentials, when required |
| `SNMP_V3_AUTH_KEY`, `SNMP_V3_PRIV_KEY` | SNMPv3 passphrases |

`WATCHDOG_CONFIG` is set in Compose, not normally in `.env`. Set logging with YAML `log_level`, not the obsolete `LOG_LEVEL` environment variable.

### 4.6 Apply configuration changes

Back up the configuration before editing. Run these from `/opt/radware/storage/scripts/CyberController-Container-Alert`:

**YAML-only change — restart the watchdog:**

```bash
docker compose restart docker-container-watchdog
```

**`.env` or Compose change — recreate the watchdog:**

```bash
docker compose up -d --no-deps --force-recreate docker-container-watchdog
```

A Python code change requires a new versioned Docker image; restarting will not update code. Neither command restarts other application containers. Check the watchdog afterward using [Section 3](#3-verify-the-installation).

## 5. Operations and maintenance

Run these commands from `/opt/radware/storage/scripts/CyberController-Container-Alert`. They manage **only the watchdog**.

### 5.1 Common commands and logs

| Task | Command |
|---|---|
| Check status | `docker compose ps` |
| View recent logs | `docker logs --tail 100 docker-container-watchdog` |
| Follow logs live | `docker logs -f docker-container-watchdog` |
| Show running image | `docker inspect docker-container-watchdog --format '{{.Config.Image}}'` |
| List installed image versions | `docker image ls watchdog` |
| Start / stop | `docker compose start docker-container-watchdog` / `docker compose stop docker-container-watchdog` |
| Restart | `docker compose restart docker-container-watchdog` |
| Recreate | `docker compose up -d --no-deps --force-recreate docker-container-watchdog` |

Stopping the watchdog **pauses monitoring** but does not stop application containers. Avoid `docker compose down` during routine maintenance.

### 5.2 Backup and restore

Before an upgrade or configuration change, back up `.env` and `watchdog-config.yaml`:

```bash
sudo install -d -m 700 backup
cp -p .env backup/.env.bak
cp -p watchdog-config.yaml backup/watchdog-config.yaml.bak

chmod 600 backup/.env.bak
chmod 600 backup/watchdog-config.yaml.bak
```

Backups contain credentials; keep the backup directory restricted. Preserve the `watchdog/` directory as it stores persistent logs and incident/HA state. Omit `sudo` if already running as root.

To restore both files, then recreate **only** the watchdog:

```bash
# Run from the watchdog installation directory
cp -p backup/.env.bak .env
cp -p backup/watchdog-config.yaml.bak watchdog-config.yaml

# Protect credentials
chmod 600 .env

# Validate Compose configuration
docker compose config --quiet

# Recreate only the watchdog
docker compose up -d --no-deps --force-recreate docker-container-watchdog

# Verify
docker compose ps docker-container-watchdog
docker compose logs --tail 50 docker-container-watchdog
```

### 5.3 Upgrade the watchdog

The upgrade uses the same installer as a new installation.

1. [Back up your configuration](#52-backup-and-restore).
2. Download or transfer the **new published version** and its `SHA256SUMS` into `/opt/radware/storage/scripts/`.
3. From that directory, verify and extract the new release **over the existing installation directory**, then run its installer:

   ```bash
   cd /opt/radware/storage/scripts
   VER=1.7.0  # Example: use the version you downloaded
   sha256sum --check SHA256SUMS
   tar -xzf "container-alert-v${VER}.tar.gz"
   cd /opt/radware/storage/scripts/CyberController-Container-Alert
   bash install.sh
   ```

4. Choose **No** when asked `Reconfigure from scratch?` to keep existing settings. Check the watchdog health and image tag using [Section 3](#3-verify-the-installation).

The package does not replace active `.env`, `watchdog-config.yaml` or `watchdog/`. Only the watchdog container is recreated. There are no automatic upgrades.

### 5.4 Rollback

If you need to return to an earlier version:

1. Check that its image is still installed: `docker image ls watchdog`.
2. If the image exists, update `WATCHDOG_VERSION` in `.env`. For example, to return to v1.5.4:

   ```bash
   cd /opt/radware/storage/scripts/CyberController-Container-Alert
   sudo sed -i 's/^WATCHDOG_VERSION=.*/WATCHDOG_VERSION=1.5.4/' .env
   grep '^WATCHDOG_VERSION=' .env
   docker compose up -d --no-deps --force-recreate docker-container-watchdog
   docker inspect docker-container-watchdog --format '{{.Config.Image}}'
   ```

   The `.env` value is `1.5.4`, **not** `watchdog:1.5.4`. Restore the matching configuration backup if necessary.

3. If the earlier image is **not** installed, obtain its release package and follow the [upgrade procedure](#53-upgrade-the-watchdog) using that older version; its installer loads the image. Choose **No** to keep existing configuration.

Verify status and logs after rollback. Restarting without selecting the older image does **not** change the code. Running a newer installer again will select the version packaged with that installer.

### 5.5 Uninstall

| Task | Command |
|---|---|
| Interactive uninstall | `bash uninstall.sh` |
| Remove watchdog, keep image and logs | `bash uninstall.sh --keep-image --keep-logs` |
| Remove watchdog and image, keep logs | `bash uninstall.sh --keep-logs` |
| Remove watchdog, image and logs | `bash uninstall.sh --remove-all` |

Logs are preserved by default. `--force` skips confirmation; use it only when unattended removal is intended. Uninstalling the watchdog does not remove CyberController application containers.

## 6. Troubleshooting

Start with the watchdog status and logs; you do not need to disrupt an application container to investigate.

```bash
docker inspect docker-container-watchdog --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker logs --tail 100 docker-container-watchdog
```

| Problem | What to check |
|---|---|
| Release download fails | The version exists on the [Releases page](https://github.com/Radware/CyberController-Container-Alert/releases); host HTTPS access, or use an offline transfer |
| Image or checksum rejected | The archive and `SHA256SUMS` come from the same release; check disk space and re-download if needed |
| Docker/Compose errors | `docker info`, `docker compose version` (or `docker-compose --version`), Docker socket permissions |
| Watchdog exits or remains unhealthy | `docker logs --tail 100 docker-container-watchdog` and `docker inspect` health status; `starting` is not `healthy` |
| No notifications | Verify receiver address, port and connectivity. For Syslog check `syslog.enabled`; for SMTP/Slack/SNMP check `alert_channels` and the channel's `enabled` field |
| SMTP/Slack authentication fails | Check the expected variable names in YAML and the actual secret values in `.env`; recreate the watchdog after `.env` changes |
| SNMPv3 traps rejected | Match NMS USM user, auth/privacy settings and sender engine ID (logged at watchdog startup) |
| INFO recovery missing | Check `alert_on_recovery`, whether an initial alert occurred, and the required stable-running interval |
| Repeated crash/restart alerts | Examine container status, Docker events and watchdog logs; do not suppress or exclude containers just to silence alerts |
| Unexpected unhealthy alerts | Compare Docker's native HEALTHCHECK with auto/manual probe details before changing any monitoring settings |
| Missing HA change alert | Check both watchdogs and the local HA exporter. The first reading creates a quiet baseline; a change should alert only after a confirmed active-node transition |

For additional information, inspect the persistent log in the installation directory:

```bash
grep 'watchdog_alert' watchdog/watchdog.log
grep -E 'ERROR|CRITICAL' watchdog/watchdog.log
```

A `WARNING` local log level can hide INFO recovery messages from the local file/console, while forwarded Syslog alerts keep their own severity. Check the receiver when investigating missing INFO events.

Do not stop, stress or restart monitored application containers merely to test notifications or silence alerts. Preserve the evidence and investigate the underlying problem before changing application settings.

## 7. Related documentation

- [Project overview](../../README.md) — product features and alert types.
- [v1.6.0 release notes](RELEASE_NOTES_V1.6.0.md) — changes and known limitations.
- [Configuration template](../../watchdog-config.yaml.example) and [environment template](../../.env.example) — settings examples.
