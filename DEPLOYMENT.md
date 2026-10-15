# CyberController Container Watchdog — Deployment

**One installer, one wizard.** The same `install.sh` supports Internet-connected and offline hosts. It deploys only the `docker-container-watchdog` service and does not change CyberController application containers.

## Before you begin

The host needs Docker, a working Docker daemon, Docker Compose, and an operator account with Docker access (and sudo where required). Git is needed only to obtain or update files from GitHub; curl is needed only if downloading the release image online.

Install only a published, approved release. The version in `VERSION` selects the corresponding prebuilt Docker image. Customer installations never compile application code.

## Install

Get the installation files in either of these ways:

**GitHub available** — clone the default `main` branch:

```bash
git clone https://github.com/Radware/CyberController-Container-Alert.git
cd CyberController-Container-Alert
```

**GitHub unavailable** — obtain the approved offline package from Radware, copy it to the host, and extract it:

```bash
tar -xzf container-alert-v1.6.0-offline.tar.gz
cd CyberController-Container-Alert
```

Then **run the same command** in either case:

```bash
bash install.sh
```

The wizard asks whether this host can download the official image from GitHub Releases.

- **Yes:** Download the Docker image and its checksum, verify the image, then continue. If the download fails, you can choose a local image or archive instead.
- **No:** Check for the matching local image and ask whether to use it. If not selected, load `watchdog.tar` from the installation directory. An offline package includes this archive and its checksum.
- **Neither available:** The installer explains what is missing and exits without changing the running watchdog. Obtain the release package and rerun `bash install.sh`.

The installer then asks for the desired notification channels (Syslog, SMTP, SNMP, Slack), the necessary connection settings, and the hostname shown in alerts. Existing `.env` and `watchdog-config.yaml` files are kept by default during upgrades.

**No Docker image build, manual `docker load`, or Compose editing is required for a normal installation.**

## Verify the installation

```bash
docker ps --filter name=docker-container-watchdog
docker inspect docker-container-watchdog --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
docker logs --tail 50 docker-container-watchdog
```

Expected: the watchdog container is running, its Docker health check becomes healthy, and recent logs show normal monitoring. Check the configured receiver for previously emitted alerts if available. A working monitor does not need an artificial failure to prove it is alive.

Read-only inspection is sufficient on operational hosts. **Do not stop, kill, restart, or stress application containers to test alerting.**

## Upgrade with the same installer

**GitHub installation:**

```bash
cd CyberController-Container-Alert
git pull --ff-only
bash install.sh
```

**Offline installation:** obtain the new offline package and update the installation files in the **existing installation directory**, preserving `.env`, `watchdog-config.yaml`, and `watchdog/`. Then run `bash install.sh` there.

On upgrade, the installer retains existing alert settings unless you explicitly request reconfiguration. It verifies the new image before changing deployment state and recreates **only the watchdog service**, preserving unrelated Docker containers. The prior image should be retained for rollback.

### Apply configuration changes

Changes to `watchdog-config.yaml` require a watchdog restart. Changes to `.env` or Compose settings require recreation to apply the new container environment:

```bash
docker compose restart docker-container-watchdog
# For changed environment or Compose settings:
docker compose up -d --no-deps --force-recreate docker-container-watchdog
```

Production monitoring configuration changes should follow the normal authorized change process.

### Roll back an image

If the previous image is still available, restore its version in `WATCHDOG_VERSION` in `.env` and recreate **only** the watchdog service:

```bash
docker image ls watchdog
docker compose up -d --no-deps --force-recreate docker-container-watchdog
```

Do not overwrite existing version tags with new code. Obtain the previous approved package if its image was removed.

## Troubleshooting

| Symptom | Check |
|---|---|
| Online image download failed | GitHub Release for the version in `VERSION` must be published and accessible; otherwise use the offline package |
| Local image rejected | The image must have a matching release version label; supply the official `watchdog.tar` |
| Cannot load image | Check available disk space, image archive, and `SHA256SUMS` |
| Docker/Compose check fails | Confirm the daemon is running and the operator has Docker access |
| Configuration is incomplete | Restore missing `.env` or `watchdog-config.yaml` from backup; installer does not discard the remaining file |
| Watchdog is not healthy | Review `docker logs --tail 100 docker-container-watchdog` and the configured notification endpoints |

For configuration examples, security considerations, image-building instructions for developers, release packaging, and advanced troubleshooting, see [Advanced Deployment Reference](docs/DEPLOYMENT_ADVANCED.md). Notification-channel examples are also in [README.md](README.md).

To remove the watchdog, run `bash uninstall.sh` from its installation directory and choose the desired retention options.
