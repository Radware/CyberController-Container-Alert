# Container Alert — Customer Installation and Operations

This guide covers **installation, configuration, day-to-day operations, upgrades, rollback, and troubleshooting**. One complete, versioned release package and one `install.sh` wizard work on hosts with or without Internet access. The installer deploys only `docker-container-watchdog`; it never restarts CyberController application containers.

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
   - [Missing recovery notifications](#61-missing-recovery-notifications)
   - [Read-only logs and probe diagnostics](#62-read-only-logs-and-probe-diagnostics)
7. [References and support](#7-references-and-support)

## 1. Prerequisites

| Requirement | What is needed |
|---|---|
| Operating system | Supported Linux host with Docker (Ubuntu 20.04 / RHEL 8 or later are the documented baseline) |
| Docker Engine | 20.10+ and a running Docker daemon |
| Docker Compose | Version 2 plugin (`docker compose`) recommended; `install.sh` also supports standalone `docker-compose` |
| Host permissions | Root or an operator with Docker access; `sudo` may be required to secure configuration files |
| Release package | Approved complete `container-alert-v<VERSION>.tar.gz` with matching `SHA256SUMS`, copied to the host |
| Network access | **Neither Git nor Internet access is required on the target host**; download from GitHub Releases elsewhere if needed |
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

**One process on every host.** Download the approved versioned package directly, or have an authorized operator transfer the same package to a disconnected CyberController host. The package already contains the prebuilt Docker image, installer, Compose files, configuration templates and documentation. No Git clone, customer-side image download or source build is needed.

The filename below uses v1.6.0 as an example. Release assets exist only after that version has been officially [published on GitHub](https://github.com/Radware/CyberController-Container-Alert/releases).

### 2.1 Get and verify the release package

**Step 1 — Obtain these two files** from the approved [GitHub Release](https://github.com/Radware/CyberController-Container-Alert/releases) (or through Radware's authorized offline transfer method):

- `container-alert-v1.6.0.tar.gz` — complete install package, including `watchdog.tar`.
- `SHA256SUMS` — checksum to verify the downloaded/transferred package.

**Step 2 — Copy both files to the target host**, into the same directory. Verify the package and extract it:

```bash
sha256sum --check SHA256SUMS
tar -xzf container-alert-v1.6.0.tar.gz
cd CyberController-Container-Alert
```

The extracted directory contains `VERSION`, `install.sh`, `docker-compose.yaml`, `watchdog.tar` and its own image checksum. Use the matching filenames for later versions.

### 2.2 Run the installer

**Step 3 — Start the configuration wizard:**

```bash
bash install.sh
```

The installer automatically verifies the included Docker image checksum and version, loads the image, then asks you to select and configure the notification channels (Syslog, SMTP, SNMP and/or Slack) and alert hostname. On an upgrade, answer **No** to “Reconfigure from scratch?” to preserve existing channel settings. It recreates **only the watchdog**, not other application containers.

**Step 4 — Confirm normal operation:**

```bash
docker ps --filter name=docker-container-watchdog
docker logs --tail 50 docker-container-watchdog
```

Expected: the watchdog is running and logging normal monitoring. For further read-only checks, see [Section 3](#3-verify-the-installation). A failed or incomplete image verification stops installation before touching the running watchdog.

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

The installer records the selected delivery channels and their settings. **Slack, SMTP, and SNMP traps** use both `alert_channels` and their channel's `enabled` setting (Slack defaults to enabled if omitted). **Syslog** instead forwards through the logging subsystem when `syslog.enabled: true`; listing it in `alert_channels` alone does not activate forwarding. All channels require a reachable receiver, and secret-based channels require credentials in `.env`.

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

#### Syslog

Set `syslog.enabled: true` and provide the receiver host, port, protocol and facility. Syslog forwards structured alert records (including INFO recoveries) independently of the local console/file log threshold. The installer may list `syslog` in `alert_channels` for consistency, but the actual forwarding switch is `syslog.enabled`.

Example receiver settings after installation:

```yaml
syslog:
  enabled: true
  host: syslog.example.com
  port: 514
  protocol: udp
  facility: local0
```

Restart the watchdog after an approved YAML-only change. Confirm receipt at the configured Syslog receiver.

#### SMTP (email)

Include `smtp` in `alert_channels` and set `smtp.enabled: true`. Configure the mail host, port, sender, recipients and `smtp.tls` in YAML. With `tls: true`, the watchdog uses SMTP STARTTLS. Use `smtp.auth: false` only for an approved unauthenticated relay. If authentication is enabled, store `SMTP_USERNAME` and `SMTP_PASSWORD` in `.env` (the YAML fields `username_env` and `password_env` reference those variable names).

#### Slack

Include `slack` in `alert_channels` and set `slack.enabled: true` (or omit `enabled`, which defaults to true for compatibility). Store the incoming webhook URL in `.env` as `SLACK_WEBHOOK_URL`; the YAML `slack.webhook_url_env` names that variable. Do not embed the webhook URL in YAML or commit it to the repository.

#### SNMP traps

Include `snmp_trap` in `alert_channels`, set `snmp_trap.enabled: true`, and configure the NMS receiver host, UDP port (normally 162) and SNMP version. SNMPv1/v2c use the configured community string; SNMPv3 credentials are referenced from `.env`. The additional details below are **receiver-side SNMP integration**, not a separate watchdog feature.

The configured `snmp_trap.version` accepts v1, v2c, or v3. For SNMPv3, the watchdog logs a stable engine ID derived from the hostname (look for `SNMP engine ID: 0x...`). Register the engine ID and the USM user at the receiving NMS; `v3_local_engine_id` can pin this identity when necessary.

Example receiver-side USM registration (adapt for the actual engine ID and secret values through the authorized NMS process):

```text
createUser -e 0x<engine-id> watchdog-user SHA "auth-passphrase" AES "priv-passphrase"
```

Both auth/privacy keys must contain at least eight characters. A privacy key requires an authentication key; SNMPv3 has no privacy-only mode. Supported authentication names include `MD5`, `SHA`, `SHA256`, `SHA384` and `SHA512`. Privacy supports `AES`, `AES192` and `AES256`; `DES` is weak and not recommended.

**Radware Container Alert trap OID:** `1.3.6.1.4.1.89.110.0.1`. The five existing var-binds are:

| OID | Object | Value |
|---|---|---|
| `1.3.6.1.4.1.89.110.1.1.0` | `cwHost` | Alerting host |
| `1.3.6.1.4.1.89.110.1.2.0` | `cwSummary` | Summary |
| `1.3.6.1.4.1.89.110.1.3.0` | `cwContainerName` | Container name |
| `1.3.6.1.4.1.89.110.1.4.0` | `cwFailureType` | `crashed` / `oom` / `unhealthy` / `restart-loop` / `recovered` / `ha-failover` |
| `1.3.6.1.4.1.89.110.1.5.0` | `cwProbeDetail` | Detection detail |

HA events reuse this MIB. They use `cwFailureType=ha-failover` and `cwContainerName=(HA cluster)`; old/new active-node names appear in summary/detail. NMS rules should match the event type, not assume every alert is a failing Docker container.

For a YAML-only channel change, restart the watchdog. For changed `.env` secrets, recreate **only** the watchdog as described in [Apply configuration changes](#46-apply-configuration-changes). Do not restart any monitored application container to verify channel delivery.

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

HA monitoring is **disabled by default**. Enabling it requires an existing supported local exporter on both nodes. The first successful reading establishes a baseline silently. Failover/failback alert delivery is best effort; consult the [HA design and validation guide](https://github.com/Radware/CyberController-Container-Alert/blob/main/docs/dev/HA_MONITORING_DESIGN.md) for limitations and approved maintenance testing.

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

Upgrades use **the same complete release package and installer** as first-time installation.

1. Back up `.env` and `watchdog-config.yaml` as in [Section 5.2](#52-backup-and-restore). Keep the persistent `watchdog/` directory and earlier approved Docker image available for rollback.
2. Obtain the **new approved** `container-alert-v<VERSION>.tar.gz` and matching `SHA256SUMS` via GitHub Releases or authorized transfer. Copy both files into the **parent directory of the existing** `CyberController-Container-Alert` installation.
3. From that parent directory, verify and extract the new package **over the existing installation files**, then run the installer:

   ```bash
   cd /path/to/parent-of/CyberController-Container-Alert
   sha256sum --check SHA256SUMS
   tar -xzf container-alert-v1.6.1.tar.gz
   cd CyberController-Container-Alert
   bash install.sh
   ```

4. Answer **No** to “Reconfigure from scratch?” to preserve configuration. The installer verifies/loads the new image, updates the pinned version, and recreates **only the watchdog**. Check its health, actual image and recent logs.

The filename v1.6.1 above is an **illustrative future version**, not a claim it exists. Replace it with the actual approved release. The archive deliberately excludes active `.env`, `watchdog-config.yaml` and `watchdog/` so upgrades preserve credentials, monitoring policy, historical logs and incident state.

There is **no automatic updater**; upgrading requires an authorized operator to run the installer. Updating Git source code is not an application upgrade.

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
| Release package missing | Obtain the complete archive and checksum from the approved GitHub Release or Radware transfer method |
| Included image/archive rejected | Check the complete package checksum, included `watchdog.tar` and internal `SHA256SUMS`, image version label and disk space |
| Docker or Compose command fails | `docker info`, Docker socket access, `docker compose version` and configured permissions |
| Only one config file exists | Restore the missing `.env` or `watchdog-config.yaml` backup; installer will not overwrite the surviving file |
| Watchdog exits or stays unhealthy | `docker inspect` health state and `docker logs --tail 100 docker-container-watchdog` |
| No alert at receiver | Confirm `alert_channels`, the matching channel's `enabled` flag, endpoints, credentials, and receiver connectivity |
| Syslog recovery not seen in local WARNING-level logs | Console/file level and forwarded Syslog alert priorities are separate; inspect receiver's structured `watchdog_alert` records |
| Unexpected unhealthy reports | Compare Docker native HEALTHCHECK, probe type and detection details; do not suppress alerts before authorized review |
| Excessive restart/crash notifications | Correlate incident and Docker event evidence; do not raise thresholds/exclude containers solely to quiet alerts |
| HA change notification missing | Check both watchdogs, local HA exporter, quorum/metrics, notification receivers and saved baseline; see [HA settings](#44-health-checks-exclusions-and-ha) |

### 6.1 Missing recovery notifications

- Confirm `alert_on_recovery: true` and that an incident previously generated an alert. A never-alarmed container has no recovery notification to send.
- For Docker-native health, inspect status using `docker inspect <container> --format '{{json .State.Health}}'`; `starting` is not `healthy`.
- For HTTP/TCP/`/proc` probes, review cached probe evidence. Crash/OOM/restart-loop recovery requires the configured stable-running interval. Do not change thresholds to force recovery.

### 6.2 Read-only logs and probe diagnostics

From the installation directory:

```bash
tail -f watchdog/watchdog.log
grep 'watchdog_alert' watchdog/watchdog.log
grep -E 'ERROR|CRITICAL' watchdog/watchdog.log
grep -E 'auto-discover|falling back' watchdog/watchdog.log
grep 'CRITICAL' watchdog/watchdog.log watchdog/watchdog.log.1
docker compose logs --tail 100 docker-container-watchdog
```

Logs rotate at approximately 10 MB with five backups. Retain `watchdog/` and
incident state; do not delete alert history simply to suppress notifications.

For authorization or assistance obtaining the release, contact the appropriate Radware support channel. Never change production CyberController service settings solely in response to a watchdog alert.

## 7. References and support

- [Project overview](../../README.md) — product behavior, alert categories and version history.
- [Release notes](RELEASE_NOTES_V1.6.0.md) — changes, compatibility and limitations for v1.6.0.
- [HA monitoring](#44-health-checks-exclusions-and-ha) — operator prerequisites and expected behavior.

- [Example watchdog YAML](../../watchdog-config.yaml.example) and [example environment variables](../../.env.example) — full annotated configuration templates.

For release access, installation assistance or product-specific guidance,
use your organization's designated Radware support contact.
