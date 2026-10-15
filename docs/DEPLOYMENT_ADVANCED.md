# Container Alert — Advanced Deployment Reference

This page supports engineers and release maintainers. For normal installation or upgrades, use the [Deployment Guide](../DEPLOYMENT.md): one interactive installer for online and offline hosts.

## Image distribution and provenance

The production installer reads the root `VERSION` file (for example `1.6.0`) and deploys only `watchdog:<VERSION>`. It never builds Docker images from source.

Official image delivery uses **GitHub Releases** at:

```text
https://github.com/Radware/CyberController-Container-Alert/releases/tag/v<VERSION>
```

Each approved release contains:

- `watchdog.tar` — the prebuilt, version-tagged Docker image.
- `SHA256SUMS` — SHA-256 hashes for the image archive and offline bundle.
- `container-alert-v<VERSION>-offline.tar.gz` — an installation folder containing `install.sh`, `VERSION`, Compose, templates, documentation, checksum, and image.

The installer verifies downloaded archive checksum and the image's `org.opencontainers.image.version` label. When an operator explicitly accepts an existing local versioned image, the installer verifies its version label. The operator remains responsible for ensuring the local image came from an approved source. An offline archive with `SHA256SUMS` is independently checksum-checked before load.

Image tags must not be reused for different source code. Build new code under a new version so rollbacks are unambiguous.

## Release publication (maintainers)

1. Finish review and merge the release changes into `main`. Do not publish directly from an unmerged release branch.
2. Confirm the existing PR CI and isolated release smoke workflow passed for the reviewed candidate.
3. Tag the approved commit as `v<VERSION>` and push the tag. The `Publish Container Alert release` GitHub Actions workflow checks that the tag matches `VERSION` and that the commit is on `main`.
4. The hosted workflow runs non-destructive tests, builds one version-labelled image, exports `watchdog.tar`, creates the offline bundle/checksums, and publishes the GitHub Release.
5. Verify the release page assets and checksum before announcing customer availability.

Publication requires repository permission for GitHub Actions to create releases (contents: write), and approval to distribute the image publicly. Until that is approved and the release workflow succeeds, use a controlled offline package instead. No release publication happens merely by opening or merging a PR.

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

Review the channel examples and settings in [README](../README.md#4-supported-alert-channels). Slack and SMTP secrets belong in `.env`; Syslog/SNMP destinations and health-check policy belong in `watchdog-config.yaml`. Do not commit local secrets to Git.

For SNMPv3, the watchdog logs a stable engine ID based on the hostname at first run. Register that engine ID and the USM user with the authorized trap receiver; if setting the optional fixed `v3_local_engine_id`, use the logged hexadecimal value. Authentication/privacy passphrases must each be at least 8 characters. The trap receiver must accept the selected auth and encryption algorithms.

For HA active-node notifications, both nodes need the supported local HA exporter and watchdog. See [HA design and validation](HA_ACTIVE_NODE_ALERT_DESIGN.md) for exporter metrics, alert behavior, and limitations. The installer does not create a Pacemaker exporter or change cluster roles.

## Failure handling

Image acquisition occurs before changing configuration or running containers. A failed download or missing offline archive does not stop the watchdog. Re-running the installer keeps existing configuration unless the operator explicitly elects to reconfigure.

If Compose reports an invalid configuration, installation stops before attempting deployment. If a container recreation fails after it starts, inspect its logs and restore the previous approved version and configuration under the normal operations process; do not assume an automatic rollback occurred.

For operational verification, use read-only Docker status, inspect and log commands in [Deployment](../DEPLOYMENT.md#verify-the-installation). **Never run destructive Docker acceptance tests on customer, shared or operational Docker hosts.** The isolated hosted acceptance instructions are in [Release Smoke Testing](RELEASE_SMOKE_TESTING.md).
