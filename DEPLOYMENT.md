# CyberController Container Watchdog — Deployment Guide

This guide covers **installation, configuration, day-to-day operations, upgrades, rollback, and troubleshooting**. The same `install.sh` wizard is used for online and offline installations. It deploys only the `docker-container-watchdog` service; it never restarts CyberController application containers.

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
   - [Online installation](#21-online-installation)
   - [Offline installation](#22-offline-installation)
3. [Verify the installation](#3-verify-the-installation)
4. [Configuration reference](#4-configuration-reference)
   - [Configuration files and editing](#41-configuration-files-and-editing)
   - [Watchdog monitoring settings](#42-watchdog-monitoring-settings)
   - [Notification channels](#43-notification-channels)
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
7. [Advanced references and support](#7-advanced-references-and-support)

## 1. Prerequisites

| Requirement | What is needed |
|---|---|
| Operating system | Supported Linux host with Docker (Ubuntu 20.04 / RHEL 8 or later are the documented baseline) |
| Docker Engine | 20.10+ and a running Docker daemon |
| Docker Compose | Version 2 plugin (`docker compose`) recommended; `install.sh` also supports standalone `docker-compose` |
| Host permissions | Root or an operator with Docker access; `sudo` may be required to secure configuration files |
| Online installation | Git access to this repository **and** HTTPS access to its published GitHub Release image; `curl` is used for the image download |
| Offline installation | Approved release package copied to the host; Git and Internet are not required |
| Alert delivery | Network connectivity to the chosen Syslog, SMTP, SNMP, or Slack destination |

Read-only prerequisite checks:

```bash
docker info
docker ps
docker compose version   # or: docker-compose --version
df -h .
```

Docker socket access is highly privileged even if a container runs as a non-root user. Restrict access to authorized operators. The installer automatically detects the socket group and configures the watchdog's existing non-root execution mode.

**Install only an approved, published release.** The version in `VERSION` must match the prebuilt image. Customer installations never compile application code. Do not run failure-injection tests on operational Docker hosts.

## 2. Installation

Choose **Online** if the host can access GitHub and download the release image. Otherwise follow **Offline**. Each procedure is complete; there are no shared steps to skip or return to.

### 2.1 Online installation

**Step 1 — Download the installation files from `main`.**

```bash
git clone https://github.com/Radware/CyberController-Container-Alert.git
cd CyberController-Container-Alert
```

**Step 2 — Start the installation wizard.**

```bash
bash install.sh
```

**Step 3 — Answer the wizard questions.**

- Answer **Yes** when asked whether the host can download the official image from GitHub Releases. The installer downloads `watchdog.tar` and `SHA256SUMS`, checks integrity and the expected image version, and loads the image.
- Choose notification channels: **Syslog**, **SMTP**, **SNMP Trap**, and/or **Slack**. Supply only the destinations and credentials requested for your selection.
- Confirm the hostname displayed in alerts. On upgrades, choose **No** at “Reconfigure from scratch?” to preserve existing settings.
- The installer creates the configuration for new installations, starts the watchdog, and checks its Docker health and image version.

If the matching GitHub Release is not yet published or is inaccessible, the wizard offers a local image/archive. Obtain the approved offline package before retrying; it will **not** build source code.

**Step 4 — Confirm the installation.**

```bash
docker ps --filter name=docker-container-watchdog
docker logs --tail 50 docker-container-watchdog
```

Expected: `docker-container-watchdog` is running and its logs show normal monitoring activity. Additional read-only checks are in [Section 3](#3-verify-the-installation).

### 2.2 Offline installation

**Step 1 — Obtain the offline package.** Get the approved `container-alert-v1.6.0-offline.tar.gz` from Radware, then transfer it to the target host. The package includes the installer, Compose file, version, prebuilt image, and checksum; no Git or image build is needed.

**Step 2 — Extract the installation files.**

```bash
tar -xzf container-alert-v1.6.0-offline.tar.gz
cd CyberController-Container-Alert
```

**Step 3 — Start the same installation wizard.**

```bash
bash install.sh
```

**Step 4 — Answer the wizard questions.**

- Answer **No** to the Internet download question.
- If the matching local `watchdog:1.6.0` image is already available, the installer asks whether to use it. Otherwise it loads the included `watchdog.tar` (and verifies the supplied checksum).
- Select Syslog, SMTP, SNMP Trap and/or Slack, enter their settings, and choose the alert hostname.
- The installer writes the configuration for a new installation, starts the watchdog, and verifies health and image version.

If neither a matching image nor the archive is available, the installer exits **without changing running containers**. Obtain the missing approved package and rerun it.

**Step 5 — Confirm the installation.**

```bash
docker ps --filter name=docker-container-watchdog
docker logs --tail 50 docker-container-watchdog
```

Expected: the watchdog is running. Check the read-only verification in [Section 3](#3-verify-the-installation) if it does not become healthy.

## 3. Verify the installation

Run these commands from the installation directory, or use the global `docker` commands from any directory:

```bash
docker compose ps
docker inspect docker-container-watchdog --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
docker inspect docker-container-watchdog --format '{{.Config.Image}}'
docker logs --tail 100 docker-container-watchdog
```

The container should be `running` and become `healthy`; the running image should be `watchdog:<VERSION>` matching the package. The installer verifies both status and the actual image ID. Startup may take several health-check intervals.

**Logs and alert delivery:**

| Location or command | Purpose |
|---|---|
| `docker logs -f docker-container-watchdog` | Live container output |
| `docker compose logs --tail 100 docker-container-watchdog` | Compose-scoped stdout |
| `./watchdog/watchdog.log` | Persistent host log, rotated at 10 MB with five backups |
| `./watchdog/state.json` | Persistent incident/HA/reboot tracking state; do not hand-edit |
| `grep 'watchdog_alert' watchdog/watchdog.log` | Search existing structured alert messages |

Also verify that the configured notification receiver can receive traffic from the host, and inspect **existing** Syslog/SMTP/SNMP/Slack records if available. No artificial container failure is necessary to verify ordinary operation.

**Optional HA installation:** On each node with `ha_monitor.enabled: true`, the local exporter can be inspected read-only using `curl -fsS http://127.0.0.1:9664/metrics` (or the configured exporter URL). The first valid reading sets a quiet baseline; no failover notification should be expected at startup. Controlled HA failover testing belongs to an approved maintenance procedure, not routine installation verification.

Never stop, kill, stress, or restart **application containers** merely to test the watchdog.

## 4. Configuration reference

The installer configures notification channels during initial setup. Use this section **after installation** if an authorized operator needs to review or change monitoring settings. No Docker image rebuild is required for a YAML or environment change.

### 4.1 Configuration files and editing

| File | What it controls | Permissions and application |
|---|---|---|
| `watchdog-config.yaml` | Monitoring thresholds, health probes, exclusions, logging, HA and notification endpoints | Installer secures it as root-owned, group-readable (mode `640`); edit with `sudoedit` or an authorized privileged editor; restart watchdog |
| `.env` | Channel secrets, alert hostname, Docker group identity and selected image version | Owner-readable only (mode `600`); edit with an authorized editor; recreate watchdog |
| `docker-compose.yaml` | Container runtime mounts, Docker socket, CPU/memory limits, networking and restart policy | Normally unchanged by customers; change only under approved support/engineering instructions |
| `watchdog-config.yaml.example` / `.env.example` | Reference templates | Not loaded by the running watchdog; don't edit these expecting the service to change |

From the installation directory:

```bash
sudoedit watchdog-config.yaml    # only if an authorized configuration change is required
nano .env                        # only if changing credentials, hostname, etc.
```

If running as root, edit directly without `sudo`. Otherwise, if `sudoedit` is unavailable, use your organization's approved privileged-editor method. Make a backup before editing. Never commit `.env`, passwords, or tokens to Git. Changes in one file do not automatically update the other.

### 4.2 Watchdog monitoring settings

These are `watchdog-config.yaml` keys. Defaults below are application defaults unless noted; installation-wizard selections can override them.

| YAML setting | Default | Explanation |
|---|---|---|
| `alert_channels` | `[slack]` in application defaults; installer prompts for selection | List of enabled delivery channel identifiers: `syslog`, `smtp`, `snmp_trap`, `slack`. Select the corresponding channel configuration too |
| `check_interval_seconds` | `60` | Interval between periodic Docker health/restart polls |
| `cooldown_minutes` | `5` | Suppresses repeated alerts for the same incident; changing it may conceal a real recurring failure |
| `restart_threshold` | `5` | Number of Docker restart-counter increases that qualifies as a restart loop |
| `restart_window_minutes` | `10` | Rolling window for restart-loop detection |
| `restart_recovery_stability_seconds` | `120` | Continuous running time needed before reporting recovery from crash/OOM/restart loop |
| `unhealthy_cycles_threshold` | `3` | Consecutive unhealthy polls required for an unhealthy alert |
| `alert_on_recovery` | `true` | Emit one informational `recovered` event when an alerted condition clears |
| `lifecycle_detection.enabled` | `true` | Correlate Docker-requested stop events with nonzero exits to reduce false crash alerts |
| `lifecycle_detection.correlation_window_seconds` | `3` | Wait briefly for matching stop evidence; supported range is 0.5–30 seconds |
| `log_level` | `INFO` | Console/file verbosity: `DEBUG`, `INFO`, `WARNING` or `ERROR`. Syslog alert severity is kept independently |
| `log_file` | `/var/log/watchdog/watchdog.log` | Persistent log file within the container, bind-mounted to `./watchdog/`; `null` disables the file log |
| `state_file` | `/var/log/watchdog/state.json` | Persists incidents, restart history and HA/reboot state across watchdog restarts; empty string disables persistence |
| `boot_grace_seconds` | `180` | Grace period after a detected host reboot before summary/normal alerts; `0` removes the grace period |
| `runbook_base_url` | Empty | Optional base URL included with alerts |

Changes to exclusions, thresholds, probe methods or suppression rules can mask real failures. Review observed evidence and obtain the applicable Radware support/engineering approval before changing those settings.

### 4.3 Notification channels

For any channel, its identifier must be present in `alert_channels` **and** its configuration must be enabled. Enabling a channel doesn't automatically prove that its destination received an alert.

| Channel | YAML field | Purpose |
|---|---|---|
| Syslog | `syslog.enabled` | Enable structured forwarding |
| Syslog | `syslog.host`, `syslog.port` | Receiver hostname/IP and listening port (commonly 514) |
| Syslog | `syslog.protocol` | `udp` or `tcp` |
| Syslog | `syslog.facility` | Syslog facility (for example `local0`) |
| SMTP | `smtp.enabled`, `smtp.auth` | Enable email; set `auth: false` for approved unauthenticated relays |
| SMTP | `smtp.host`, `smtp.port` | Mail server and port |
| SMTP | `smtp.sender`, `smtp.recipients` | Sender address and YAML list of recipient addresses |
| SMTP | `smtp.tls` | Whether to use SMTP TLS |
| SMTP | `smtp.username_env`, `smtp.password_env` | Names of secret environment variables, normally `SMTP_USERNAME` and `SMTP_PASSWORD` |
| Slack | `slack.enabled` | Enable Slack notifications |
| Slack | `slack.webhook_url_env` | Name of the secret environment variable, normally `SLACK_WEBHOOK_URL` |
| SNMP | `snmp_trap.enabled` | Enable trap dispatch |
| SNMP | `snmp_trap.host`, `snmp_trap.port` | NMS/trap receiver and destination UDP port (normally 162) |
| SNMP | `snmp_trap.version`, `snmp_trap.community` | SNMP `v1`/`v2c`/`v3`; community applies to v1/v2c only |
| SNMP | `snmp_trap.trap_oid` | Optional trap OID; defaults to the Radware Container Alert notification OID |
| SNMPv3 | `v3_username`, `v3_auth_protocol`, `v3_priv_protocol` | USM user and authentication/privacy algorithms under `snmp_trap` |
| SNMPv3 | `v3_auth_key_env`, `v3_priv_key_env` | Names of passphrase variables in `.env`; do not embed secrets in YAML |
| SNMPv3 | `v3_local_engine_id` | Optional fixed sender engine ID used by receiving NMS registration |

Example: changing the Syslog destination **after installation**:

```yaml
alert_channels:
  - syslog

syslog:
  enabled: true
  host: syslog.example.com
  port: 514
  protocol: udp
  facility: local0
```

When enabling Syslog **alongside** existing channels, add `syslog` to the existing list rather than replacing the list as shown in this single-channel example. Restart the watchdog after a YAML-only change.

For SMTP authentication, store the username/password in `.env`; for Slack, store the webhook in `.env`. SNMPv3 passphrases also belong in `.env` and must meet protocol requirements (at least eight characters). See [Advanced Deployment](docs/DEPLOYMENT_ADVANCED.md) for SNMPv3 receiver/engine-ID setup and the supported enterprise trap fields.

### 4.4 Health checks, exclusions, and HA

| YAML setting | Default | Explanation |
|---|---|---|
| `auto_health_check.enabled` | `true` in installer | Enable automatic HTTP/TCP/discovery probes for containers without a native Docker health check |
| `auto_health_check.paths` | `/-/healthy`, `/health`, `/healthz`, `/api/health`, `/metrics`, `/` | HTTP paths attempted during endpoint discovery |
| `auto_health_check.timeout_seconds` | `2` in installer | Probe timeout after endpoint discovery |
| `container_health_checks` | `{}` | Per-container `http` or `exec` probe overrides; take precedence over automatic discovery |
| `excluded_containers` | `[]` | Names excluded from alerting entirely; not a routine noise-management setting |
| `ignored_container_events` | R&D-labelled temporary syntax-check event | Narrow, label-based exceptions for intentional temporary operations; preserve normal monitoring |
| `ha_monitor.enabled` | `false` | Optional CyberController HA active-node change notification, installed on both nodes |
| `ha_monitor.exporter_url` | `http://127.0.0.1:9664/metrics` | Local HA metrics exporter endpoint; doesn't install the exporter |

For an approved custom HTTP probe, the shape is:

```yaml
container_health_checks:
  example-api:
    type: http
    internal_port: 8080
    path: /health
    timeout_seconds: 5
```

The supported per-container probe fields are:

| Field under `container_health_checks.<name>` | Meaning |
|---|---|
| `type` | `http` (default) or `exec` |
| `internal_port` | Required for HTTP checks; internal container listening port |
| `path` | HTTP request path (defaults to `/`) |
| `command` | Command to run inside the container for `exec` probes; required for this type |
| `timeout_seconds` | Timeout for either method (default 5 seconds) |

Use the *actual* container name and a supported health endpoint only after validation. Manual `type: exec` checks execute commands inside monitored containers, so they must be approved and carefully reviewed. Do not add exclusions or broad ignore rules merely to silence unexpected alerts.

HA monitoring is **disabled by default**. Enabling it requires an existing supported local exporter on both nodes. The first successful reading establishes a baseline silently. Failover/failback alert delivery is best effort; consult the [HA design and validation guide](docs/HA_ACTIVE_NODE_ALERT_DESIGN.md) for limitations and approved maintenance testing.

### 4.5 Environment variables

Variables in `.env` are read when the container is created. The installer sets the image version, runtime identity and alert hostname automatically; credentials appear only for selected channels.

| Environment variable | Purpose | When needed |
|---|---|---|
| `WATCHDOG_HOST` | Hostname displayed in alerts | Generated by installer; change only when the alert identity needs correction |
| `WATCHDOG_VERSION` | Pinned local image tag `watchdog:<version>` | Managed by installer; used for controlled upgrades/rollback |
| `WATCHDOG_UID`, `WATCHDOG_GID` | Non-root runtime user and group (normally 1000/1000) | Set by installer; do not change casually |
| `DOCKER_GID` | Group ID of the host Docker socket | Detected automatically by installer |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook secret | If Slack enabled |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Mail credentials or app token | If SMTP authentication enabled |
| `SNMP_V3_AUTH_KEY` | SNMPv3 authentication passphrase | For SNMPv3 authNoPriv/authPriv |
| `SNMP_V3_PRIV_KEY` | SNMPv3 encryption passphrase | For SNMPv3 authPriv |
| `WATCHDOG_CONFIG` | Container-internal config path set explicitly in Compose | Normally `/etc/watchdog/watchdog-config.yaml`; not an everyday `.env` setting |

**Logging clarification:** `LOG_LEVEL` in older `.env` files is not used by the application. Change `log_level` in `watchdog-config.yaml` instead. Do not store passwords or webhook values in YAML or version control.

### 4.6 Apply configuration changes

First back up the relevant files ([Section 5.2](#52-backup-and-restore)) and have an authorized operator review the change.

| What changed | Command | New Docker image needed? |
|---|---|---|
| `watchdog-config.yaml` only | `docker compose restart docker-container-watchdog` | No |
| `.env` (secrets, hostname, image version) | `docker compose up -d --no-deps --force-recreate docker-container-watchdog` | No rebuild; requested image must already exist |
| `docker-compose.yaml` runtime settings | Same service-only Compose recreation, after engineering approval | No rebuild |
| Application Python code | Deploy a newly tested, versioned release image | **Yes — a different image**, not merely a restart |

```bash
# After an approved YAML-only change:
docker compose restart docker-container-watchdog

# After an approved .env or Compose change:
docker compose up -d --no-deps --force-recreate docker-container-watchdog
```

If both YAML and `.env` changed, use the recreation command to apply both. Always confirm health and inspect logs afterward. Updating a Git checkout alone does **not** change Python code inside the running image.

## 5. Operations and maintenance

All commands below target **only the watchdog**. Run Compose commands from the directory containing `docker-compose.yaml` and `.env` (use `docker-compose` in place of `docker compose` on a legacy standalone installation).

### 5.1 Common commands and logs

| Operation | Command |
|---|---|
| Show container status | `docker compose ps` |
| Show recent output | `docker logs --tail 100 docker-container-watchdog` |
| Follow logs live | `docker logs -f docker-container-watchdog` |
| Inspect running image tag | `docker inspect docker-container-watchdog --format '{{.Config.Image}}'` |
| List installed image versions | `docker image ls watchdog` |
| Start the watchdog | `docker compose start docker-container-watchdog` |
| Stop watchdog for approved maintenance | `docker compose stop docker-container-watchdog` |
| Restart watchdog | `docker compose restart docker-container-watchdog` |
| Recreate only watchdog | `docker compose up -d --no-deps --force-recreate docker-container-watchdog` |

Stopping the watchdog **pauses alert monitoring**. It does not stop application containers. Avoid project-wide `docker compose down` in routine operations.

### 5.2 Backup and restore

Before upgrades or authorized configuration changes, preserve the configuration and secrets in a restricted local directory:

```bash
mkdir -p -m 700 backup
cp -p .env backup/.env.bak
sudo cp -p watchdog-config.yaml backup/watchdog-config.yaml.bak
```

The `watchdog/` directory contains persistent logs and incident state: keep it intact across upgrades and rollbacks. The installer also creates a dated backup of both config files when you **explicitly** choose to reconfigure from scratch.

To restore both files after an approved rollback, preserve their original permissions and recreate **only** the watchdog:

```bash
cp -p backup/.env.bak .env
sudo cp -p backup/watchdog-config.yaml.bak watchdog-config.yaml
docker compose up -d --no-deps --force-recreate docker-container-watchdog
```

If running as root, omit `sudo`. Keep backup directories access-restricted because `.env` contains credentials.

### 5.3 Upgrade the watchdog

**Online upgrade**

1. Back up configuration using [Section 5.2](#52-backup-and-restore).
2. In the installation directory, update the checked-out `main` branch:

   ```bash
   git pull --ff-only
   bash install.sh
   ```

3. Answer **Yes** for the online image download. When the installer detects the existing config, answer **No** to reconfigure from scratch; it preserves channel settings while updating the image version.
4. Check health, actual running image, and recent logs using [Section 3](#3-verify-the-installation).

**Offline upgrade**

1. Back up configuration and retain `watchdog/`.
2. Transfer the **new** approved offline bundle, unpack it into a separate temporary folder, then copy only the new installation/release files into the existing installation directory (never overwrite `.env`, `watchdog-config.yaml`, or `watchdog/`):

   ```bash
   mkdir -p /tmp/container-alert-upgrade
   tar -xzf container-alert-v1.6.0-offline.tar.gz -C /tmp/container-alert-upgrade
   cd /path/to/existing/CyberController-Container-Alert
   cp /tmp/container-alert-upgrade/CyberController-Container-Alert/{install.sh,uninstall.sh,docker-compose.yaml,VERSION,watchdog.tar,SHA256SUMS,README.md,DEPLOYMENT.md,.env.example,watchdog-config.yaml.example} .
   mkdir -p docs
   cp /tmp/container-alert-upgrade/CyberController-Container-Alert/docs/DEPLOYMENT_ADVANCED.md docs/
   bash install.sh
   ```

3. Answer **No** to Internet download. Use the supplied archive (or approved matching local image), and answer **No** to reconfiguration to keep current settings.
4. Verify health and the running image, as with the online upgrade.

Use the versioned package for the release you are actually upgrading to; `v1.6.0` above is an example. Older image tags should remain available for rollback. **There is no automatic updater**: upgrades happen only when an operator runs the installer.

### 5.4 Rollback

If a new watchdog image fails after an upgrade, use the prior approved version, provided it remains available locally:

1. Record the failure and inspect `docker logs --tail 100 docker-container-watchdog`.
2. Verify the previous image exists: `docker image ls watchdog`. If absent, obtain/load the previous approved image through the authorized offline process.
3. Restore the previous `WATCHDOG_VERSION` in `.env` (for example, `1.5.4` when rolling back from v1.6.0) and, if necessary, restore its saved YAML/credential configuration.
4. Recreate **only** the watchdog and verify it:

   ```bash
   docker compose up -d --no-deps --force-recreate docker-container-watchdog
   docker ps --filter name=docker-container-watchdog
   docker logs --tail 50 docker-container-watchdog
   ```

An ordinary `git checkout` or restart does **not** roll back running code; the selected image controls it. Running a newer `install.sh` afterward can reset `WATCHDOG_VERSION` to its `VERSION` file, so use the corresponding approved package for a lasting rollback.

### 5.5 Uninstall

The installer package includes `uninstall.sh`. Its options govern whether images and historical logs are retained.

| Desired result | Command |
|---|---|
| Interactive uninstall with confirmation | `bash uninstall.sh` |
| Remove watchdog, keep image and logs | `bash uninstall.sh --keep-image --keep-logs` |
| Remove watchdog/image, preserve logs | `bash uninstall.sh --keep-logs` |
| Remove container, image, and logs | `bash uninstall.sh --remove-all` |
| Fully non-interactive removal, **including logs** | `bash uninstall.sh --remove-all --force` |

Do not use `--remove-all` if historical alerts or state must be retained. Uninstall affects the watchdog, not CyberController application containers.

## 6. Troubleshooting

| Observation | What to check |
|---|---|
| Installer cannot reach GitHub Releases | Host HTTPS access and the published release matching `VERSION`; use the approved offline archive if disconnected |
| Local image/archive rejected | Correct `watchdog:<VERSION>` tag, release version label, archive checksum and disk space |
| Docker or Compose command fails | `docker info`, Docker socket access, `docker compose version` and configured permissions |
| Only one config file exists | Restore the missing `.env` or `watchdog-config.yaml` backup; installer will not overwrite the surviving file |
| Watchdog exits or stays unhealthy | `docker inspect` health state and `docker logs --tail 100 docker-container-watchdog` |
| No alert at receiver | Confirm `alert_channels`, the matching channel's `enabled` flag, endpoints, credentials, and receiver connectivity |
| Syslog recovery not seen in local WARNING-level logs | Console/file level and forwarded Syslog alert priorities are separate; inspect receiver's structured `watchdog_alert` records |
| Unexpected unhealthy reports | Compare Docker native HEALTHCHECK, probe type and detection details; do not suppress alerts before authorized review |
| Excessive restart/crash notifications | Correlate incident and Docker event evidence; do not raise thresholds/exclude containers solely to quiet alerts |
| HA change notification missing | Both watchdogs, local HA exporter, quorum/metrics, notification receivers and saved baseline; see advanced HA guide |

For authorization or assistance obtaining the offline release, contact the appropriate Radware support channel. Never change production CyberController service settings solely in response to a watchdog alert.

## 7. Advanced references and support

- [README](README.md) — product behavior, failure types, architecture and version history.
- [Advanced Deployment](docs/DEPLOYMENT_ADVANCED.md) — developer image builds, release publication, security, SNMPv3 receiver setup.
- [HA alert design and validation](docs/HA_ACTIVE_NODE_ALERT_DESIGN.md) — HA prerequisites, validation, limitations.
- [Release smoke testing](docs/RELEASE_SMOKE_TESTING.md) — isolated **developer/CI-only** acceptance tests. Never run destructive scenarios on an operational Docker host.
- [Example watchdog YAML](watchdog-config.yaml.example) and [example environment variables](.env.example) — full annotated configuration templates.

For offline-image access or product-specific guidance, use your designated Radware support contact (the previous deployment guide listed `northamericare@radware.com`).
