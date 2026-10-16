# Container Alert — Development and Release Guide

**For repository maintainers.** This is the repeatable development → testing → approval → GitHub Release process, including the exact finalization checklist for **v1.6.0**. Customer installation instructions belong in [Deployment](../DEPLOYMENT.md).

## Table of contents

1. [How source, images and packages relate](#1-how-source-images-and-packages-relate)
2. [Normal development cycle](#2-normal-development-cycle)
3. [Preparing the next numbered release](#3-preparing-the-next-numbered-release)
4. [v1.6.0 finalization checklist](#4-v160-finalization-checklist)
5. [Publishing and verifying v1.6.0](#5-publishing-and-verifying-v160)
6. [After release and rollback](#6-after-release-and-rollback)
7. [Release safety and best practices](#7-release-safety-and-best-practices)

## 1. How source, images and packages relate

| Item | v1.6.0 example | Purpose |
|---|---|---|
| Development/stabilization branch | `release/v1.6.0` | Code and documents under review |
| Source version file | `VERSION` = `1.6.0` | Single source of truth for the image and asset filenames |
| Final Git tag | `v1.6.0` | Immutable reference to the approved `main` commit |
| Docker image | `watchdog:1.6.0` | Prebuilt application and Python dependencies; customers do not build this |
| Online package | `container-alert-v1.6.0-online.tar.gz` | Installer, Compose, templates and docs; no code or image |
| Offline package | `container-alert-v1.6.0-offline.tar.gz` | Identical installer plus the saved Docker image and checksum |
| Release integrity manifest | `SHA256SUMS` | SHA-256 for `watchdog.tar` and both release packages |

**Source code changes do not upgrade customers.** Customer upgrades occur only when a new numbered, tested image is published and the installer deploys that image.

The hosted tag workflow reads `VERSION`, builds and tests the image, packages both installers, generates checksums, creates a **draft** GitHub Release, verifies all four assets exist, then publishes it. The published assets are:

```text
watchdog.tar
SHA256SUMS
container-alert-v1.6.0-online.tar.gz
container-alert-v1.6.0-offline.tar.gz
```

The published release also uses the version-specific release notes when `docs/RELEASE_NOTES_V1.6.0.md` exists. No changes to `install.sh` or download URLs are required for v1.6.1 and later.

## 2. Normal development cycle

**For a new fix or feature after v1.6.0 is released:**

1. Start with the latest `main`, and create a short-lived feature branch:

   ```bash
   git switch main
   git pull --ff-only origin main
   git switch -c fix/describe-change
   ```

2. Change Python code, unit tests, configuration examples or documentation. Do not reuse an existing release tag or overwrite a versioned image with different code.

3. Validate with **non-destructive** checks only:

   ```bash
   bash -n install.sh uninstall.sh scripts/package_release.sh
   python3 -m unittest discover -s tests -q
   ```

   Run Python tests in a development environment with the required packages installed. Unit tests and fake-Docker installer/package tests must not use an operational Docker daemon. Do not run `tests/release-smoke.sh` directly on an appliance or shared host.

4. Push and open a PR targeting `main`; obtain code review:

   ```bash
   git push -u origin fix/describe-change
   gh pr create --base main --head fix/describe-change --fill
   ```

5. Check normal GitHub CI. For major changes or the final release candidate, require the **[Release smoke (isolated)](../.github/workflows/release-smoke.yml)** hosted workflow on the release PR. It is the only supported location for destructive Docker acceptance tests.

**While v1.6.0 is being stabilized:** feature/fix PRs may target `release/v1.6.0`; the release branch itself is reviewed through [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) targeting `main`. Don't merge unreviewed work directly into a published release.

## 3. Preparing the next numbered release

After v1.6.0 is published, the next compatible maintenance release would typically be **v1.6.1**. A feature release might instead be v1.7.0; breaking changes require special compatibility review.

1. Choose the version and update `VERSION` (e.g. `1.6.1`) in the release candidate.
2. Add the matching version entry in the README version history (required by CI).
3. Create/update `docs/RELEASE_NOTES_V1.6.1.md` describing all changes since the previous published release, customer-visible behavior, tests and limitations.
4. Use a release PR to `main` if batching fixes. Validate the actual final PR head in CI and hosted isolated smoke.
5. Merge only with approval. Then create the matching tag from `main`. **Do not** tag a release branch or publish from an unmerged PR.
6. The **same** `scripts/package_release.sh`, GitHub Actions workflow and installer produce the correct versioned assets automatically.

**Development iterations are not releases:** changes can be committed and tested many times without publishing an image for each commit. Release numbering happens when preparing an approved delivery.

## 4. v1.6.0 finalization checklist

**Status when this guide was prepared (October 16, 2026):** `main` is still v1.5.4, `release/v1.6.0` contains `VERSION=1.6.0`, and PR #9 is **open/unmerged**. The GitHub Release for v1.6.0 has not been published by this work.

Do **not** push the tag until every applicable item below is satisfied:

- [ ] PR #9 receives final engineering review and approval, including lifecycle, alerting, HA, security and release packaging.
- [ ] Normal CI and [isolated hosted Docker smoke](RELEASE_SMOKE_TESTING.md) both pass **for the current PR head SHA**. Rerun if release-impacting code changes.
- [ ] The release notes and customer alert text describe all changes since v1.5.4; there are no private lab hostnames/addresses or unapproved secrets in release files.
- [ ] Release approval explicitly permits publication of **Docker image and packages to the public GitHub repository**. A published release can be downloaded by anyone.
- [ ] Deployment and rollback instructions are reviewed; HA failover and customer notification destinations receive any required authorized, separate acceptance. A passing hosted smoke does not prove live customer SMTP/Slack/SNMP delivery.
- [ ] The `main` branch contains the release workflow and the full approved PR content. Tagging before merge will not publish this release as intended.

Useful inspection commands (read-only):

```bash
gh pr view 9 -R Radware/CyberController-Container-Alert
gh pr checks 9 -R Radware/CyberController-Container-Alert
gh run list -R Radware/CyberController-Container-Alert \
  --workflow release-smoke.yml --limit 5
```

If checks fail or public distribution is not authorized, **do not merge/tag/publish**. Fix the PR and rerun checks.

## 5. Publishing and verifying v1.6.0

The commands below are **release-maintainer actions**. Run them only **after** approval. They are documented here; they are **not** executed as part of this documentation update.

### Step A — Merge and verify main

Merge [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) using the approved GitHub review process. Then update a clean repository checkout:

```bash
git fetch origin main --tags
git switch main
git pull --ff-only origin main
cat VERSION                                 # must be 1.6.0
git status --short                          # must be empty
git log -1 --oneline                        # record final release commit SHA
git ls-remote --tags origin refs/tags/v1.6.0
```

The final command must return **no existing v1.6.0 tag**. If it exists, investigate; do not move or overwrite release tags.

### Step B — Create the release tag

From the clean, approved `main` commit:

```bash
test "$(cat VERSION)" = "1.6.0" || exit 1
git tag -a v1.6.0 -m "Container Alert v1.6.0"
git push origin v1.6.0
```

This tag push triggers [Publish Container Alert release](../.github/workflows/release-package.yml) on a GitHub-hosted runner. The workflow verifies that the tag matches `VERSION` and points into `main` history, runs unit tests, builds the version-labelled Docker image, checks imports, creates both release packages and checksum manifest, and publishes **after** checking all assets.

### Step C — Check the GitHub workflow and release

```bash
gh run list -R Radware/CyberController-Container-Alert \
  --workflow release-package.yml --limit 5
gh release view v1.6.0 -R Radware/CyberController-Container-Alert
```

Check that the workflow completed successfully and that the release contains **all four assets**, the intended release notes, and no customer-specific secrets.

Download and verify the release files **without installing or touching Docker**:

```bash
mkdir -p /tmp/container-alert-v1.6.0-verification
gh release download v1.6.0 -R Radware/CyberController-Container-Alert \
  --dir /tmp/container-alert-v1.6.0-verification
cd /tmp/container-alert-v1.6.0-verification
sha256sum --check SHA256SUMS
tar -tzf container-alert-v1.6.0-online.tar.gz | head
tar -tzf container-alert-v1.6.0-offline.tar.gz | grep watchdog.tar
```

Verify the online archive contains no Python application source and no `watchdog.tar`; the offline archive must contain the same installer plus `watchdog.tar` and its internal checksum. Confirm both carry `VERSION=1.6.0`.

**If publication fails:** investigate the GitHub Actions logs and any draft release. Do not tell customers the release is ready. Do not force-move an existing published tag; follow the organization's correction/versioning process.

### Step D — Announce and distribute

- **Online operators** download `container-alert-v1.6.0-online.tar.gz` plus `SHA256SUMS` from the published release page. They run the contained `bash install.sh` wizard and answer **Yes** to download the image.
- **Offline operators** receive `container-alert-v1.6.0-offline.tar.gz` plus `SHA256SUMS` through an approved transfer method. They run the **same wizard** and answer **No**.
- Keep the previous approved image available on target systems for rollback. Do not stop or alter CyberController application containers during installation.

See [Deployment](../DEPLOYMENT.md#2-installation) for the complete customer procedures.

## 6. After release and rollback

An online/offline customer upgrade obtains the **new release package** and runs its installer in the existing installation directory; the package intentionally omits active `.env`, `watchdog-config.yaml` and the persistent `watchdog/` directory so customer settings and incident history are retained. The installer recreates only the watchdog container.

For YAML-only monitoring changes, restart the watchdog. For `.env` or Compose changes, recreate just the watchdog. For Python changes, build and publish **a different numbered image**. A source-code pull or restart does not update Python inside the running image.

If rollback is authorized, select the last approved `WATCHDOG_VERSION` and its corresponding stored image/configuration. See [Deployment — Rollback](../DEPLOYMENT.md#54-rollback). Never rewrite an old published tag to mean new code.

## 7. Release safety and best practices

This project uses a standard GitHub Flow / tagged-release approach: review changes by PR, test them in CI, merge approved code to `main`, assign a semantic version/tag, and distribute **prebuilt artifacts** rather than compiling on customer systems.

Essential rules:

- **Approval before publication:** this repository is public. Confirm Radware authorizes distributing the code, Docker image, release notes and package contents.
- **Traceability:** align `VERSION`, the Git tag, the Docker image label/tag, both installer package names, CI results and published checksums. Do not reuse image tags.
- **Safety:** all Docker failure injection must stay inside [isolated hosted CI](RELEASE_SMOKE_TESTING.md), never an operational or shared Docker daemon. Test installers/packages with fake commands and tiny dummy image files.
- **Integrity:** SHA-256 manifests check whether package bytes match the release assets; they are not a replacement for trusted distribution, source review, or stronger artifact signatures.
- **Production restraint:** no source builds, no automatic updates, no service-wide Docker shutdowns, and no customer-directed changes to CyberController Compose/resource policy.

For mature supply-chain requirements, Radware may later add protected `v*` tags, immutable GitHub Releases, pinned image dependencies and signed provenance. The release image is built anew from the approved tag, so it is **not guaranteed byte-for-byte identical** to the image tested in the PR while its base/dependency versions can change. Record its final digest and obtain the appropriate release sign-off. Those harder supply-chain enhancements are separate from this deliberately small v1.6.0 delivery flow.

Official references: [Managing GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository), [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow), and [immutable releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
