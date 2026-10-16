# Container Alert — Advanced Deployment Reference

This page supports engineers and release maintainers. For normal installation and upgrades, use the [Deployment Guide](../DEPLOYMENT.md). For branching, CI and release publication, see [Development Guide](DEVELOPMENT.md).

## Image distribution and provenance

The production installer reads the root `VERSION` file (for example `1.6.0`) and deploys only `watchdog:<VERSION>`. It never builds Docker images from source.

Official image delivery uses **GitHub Releases** at:

```text
https://github.com/Radware/CyberController-Container-Alert/releases/tag/v<VERSION>
```

Each approved release contains **two downloadable assets**:

- `container-alert-v<VERSION>.tar.gz` — complete installer, `VERSION`, Compose, templates, documentation, the prebuilt `watchdog.tar` image, and its internal `SHA256SUMS`.
- `SHA256SUMS` — checksum of the entire packaged archive, checked before extraction.

The [release packaging script](../scripts/package_release.sh) produces the same complete package for connected and disconnected customer hosts. No app Python source or active operator configuration (`.env`, `watchdog-config.yaml` or `watchdog/`) is included.

The operator first checks the package checksum, then extracts the archive and runs `bash install.sh`. The installer **requires** the included image archive and internal checksum, verifies them and the image's `org.opencontainers.image.version` label, and loads `watchdog:<VERSION>`. No installer-side Git, Internet connection or source build is needed.

Image tags must not be reused for different source code. Build new code under a new version so rollbacks are unambiguous.

## Release publication (maintainers)

Use the version-independent [Development Guide](DEVELOPMENT.md) and, for v1.6.0, the [finalization checklist](RELEASE_V1.6.0_CHECKLIST.md). Approve and merge the release PR into `main`, check latest CI and isolated smoke results, obtain authorization for public distribution, then push the approved matching `v<VERSION>` tag. The hosted release workflow validates/tests/builds the image, assembles the complete archive and external checksum, uploads both to a draft GitHub Release, verifies the assets, and publishes.

Tagging does not deploy to an operational host. Customer packages do not exist until the tagged release workflow succeeds.

## Developer-only source build

Production operators should never build source as part of installation. In a development environment only:

```bash
export WATCHDOG_VERSION="$(cat VERSION)"
docker compose -f docker-compose.build.yaml build
```

The build file passes `WATCHDOG_VERSION` into the image metadata. This produces a local image; it does **not** publish a GitHub Release or update an existing running container. The Dockerfile packages `watchdog.py`, `lifecycle.py`, and `ha_monitor.py` inside the image; a container restart alone does not apply host-side Python edits.

## Configuration files

The wizard generates and retains two files:

| File | Purpose | Apply changes |
|---|---|---|
| `watchdog-config.yaml` | Alert channels, endpoints, checks and thresholds | Restart only the watchdog |
| `.env` | Authentication secrets, image version, hostname and Docker group settings | Recreate only the watchdog |

The installer detects the group ID of `/var/run/docker.sock` and configures its existing non-root runtime permissions. The production Compose file maintains a read-only root filesystem, capability restrictions, CPU/memory limits and persistent log volume.

For the full operator-facing channel and settings reference, see [Deployment](../DEPLOYMENT.md#4-configuration-reference). Slack and SMTP secrets belong in `.env`; Syslog/SNMP destinations and health-check policy belong in `watchdog-config.yaml`. Do not commit local secrets to Git.

For HA active-node notifications, both nodes need the supported local HA exporter and watchdog. See [HA design and validation](HA_ACTIVE_NODE_ALERT_DESIGN.md) for exporter metrics, alert behavior, and limitations. The installer does not create a Pacemaker exporter or change cluster roles.

## SNMP trap receiver reference

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

## Container-event filtering reference

The intended MariaDB HA syntax-check helper carries the explicit label `com.radware.cybercontroller.role=mariadb-ha-syntax-check`. The configured `ignored_container_events` policy ignores only the designated temporary helper `die` event; normal service container failures remain monitored.

A narrow compatibility fallback exists for dynamic MariaDB dump helpers: it applies only when the helper image, non-primary container name, Docker `AutoRemove` status, expected SQL dump file mount and exit status 137 **all** match. This is not a general rule to suppress MariaDB failures. Any change to monitoring exceptions belongs to Radware R&D/authorized operations, not routine customer alert handling.

## Failure handling

Image acquisition occurs before changing configuration or running containers. A failed download or missing offline archive does not stop the watchdog. Re-running the installer keeps existing configuration unless the operator explicitly elects to reconfigure.

If Compose reports an invalid configuration, installation stops before attempting deployment. If a container recreation fails after it starts, inspect its logs and restore the previous approved version and configuration under the normal operations process; do not assume an automatic rollback occurred.

For operational verification, use read-only Docker status, inspect and log commands in [Deployment](../DEPLOYMENT.md#3-verify-the-installation). **Never run destructive Docker acceptance tests on customer, shared or operational Docker hosts.** The isolated hosted acceptance instructions are in [Release Smoke Testing](RELEASE_SMOKE_TESTING.md).
