# Container Alert — Development and Release Guide

**Reusable for every release.** This guide goes from changing source code to publishing and verifying a customer installation package. For the specific approval status of v1.6.0, see [its release checklist](RELEASE_V1.6.0_CHECKLIST.md). Customers follow [Deployment](../operations/INSTALLATION_AND_OPERATIONS.md).

## Table of contents

1. [What we publish](#1-what-we-publish)
2. [Make and test a change](#2-make-and-test-a-change)
3. [Prepare the next version](#3-prepare-the-next-version)
4. [Review, merge and tag](#4-review-merge-and-tag)
5. [Verify the published package](#5-verify-the-published-package)
6. [Customer delivery and rollback](#6-customer-delivery-and-rollback)
7. [Safety and best practices](#7-safety-and-best-practices)
8. [Developer-only builds and monitoring notes](#8-developer-only-builds-and-monitoring-notes)

## 1. What we publish

The source repository contains the Python application, tests, installer and release automation. **Customer hosts get none of the application Python source.** A tagged release builds one versioned image and creates two public release assets:

| Asset | Contents |
|---|---|
| `container-alert-v<VERSION>.tar.gz` | Installer, VERSION, Compose, templates, operator docs, prebuilt `watchdog.tar` and the image's internal `SHA256SUMS` |
| `SHA256SUMS` | SHA-256 checksum of the complete release archive |

All versions use the **same workflow, package format and installer**. A release such as `1.6.1` uses `VERSION=1.6.1`, Git tag `v1.6.1`, image `watchdog:1.6.1` and package `container-alert-v1.6.1.tar.gz`. These are examples, not claims that a version has been published.

Changes in Git do **not** upgrade running customer containers. Publication and installation are separate actions.

## 2. Make and test a change

**Step 1 — Create a development branch in a clean checkout.**

```bash
git switch main
git pull --ff-only origin main
git switch -c fix/describe-change
```

Use `feature/...`, `fix/...` or `docs/...`. If there is an active stabilization branch for an upcoming release, coordinate whether your PR should target that release branch instead of main. Multiple worktrees are supported.

**Step 2 — Change code, tests and documentation.** Typical files include `watchdog.py`, `lifecycle.py`, `ha_monitor.py`, `install.sh`, `scripts/package_release.sh`, and `tests/`. Don't reuse a previously published Docker image version for changed Python code.

**Step 3 — Run non-destructive tests on a development workstation.**

```bash
bash -n install.sh uninstall.sh scripts/package_release.sh
python3 -m unittest discover -s tests -q
git diff --check
```

The Python test environment needs the repo's test dependencies (including Docker SDK, Requests, PyYAML). Installer/package tests use fake Docker. **Never** run real failure-injection tests or `tests/release-smoke.sh` on operational/shared Docker hosts.

**Step 4 — Commit, push, request review.**

```bash
git add <changed-files>
git commit -m "fix: describe the change"
git push -u origin fix/describe-change
gh pr create --base main --head fix/describe-change --fill
```

Substitute your branch/files. PRs should cover behavior, tests, customer impact and safety. Review results:

```bash
gh pr view <PR_NUMBER> -R Radware/CyberController-Container-Alert
gh pr checks <PR_NUMBER> -R Radware/CyberController-Container-Alert
```

Normal GitHub CI runs unit/static/build checks. Only the [hosted isolated smoke workflow](../../.github/workflows/release-smoke.yml) may perform destructive real-Docker acceptance. For release-critical changes it must pass at the latest reviewed PR head. Code review and approval are still required when checks pass.

## 3. Prepare the next version

Several changes may ship together. When ready to publish, choose a **new** version (e.g. `1.6.1` for a compatible fix; `1.7.0` for a compatible feature).

```bash
NEXT_VER=1.6.1    # set the desired NEW version here each time
git switch main
git pull --ff-only origin main
git switch -c "release/v$NEXT_VER"
printf '%s\n' "$NEXT_VER" > VERSION
```

Update the following to match the chosen version:

1. `VERSION` — set the new number (the command above does this).
2. `README.md` — add the matching version-history row; CI checks this.
3. `docs/operations/RELEASE_NOTES_V<VERSION>.md` — create the matching version-specific release notes, including all changes since the previous published release, operational impact, limitations and test evidence.
4. Deployment and examples if operator procedures/settings changed; keep private lab/customer identifiers and secrets out.
5. Tests and hosted smoke approval for the release PR.

```bash
git add VERSION README.md "docs/operations/RELEASE_NOTES_V$NEXT_VER.md"
git commit -m "release: prepare v$NEXT_VER"
git push -u origin "release/v$NEXT_VER"
gh pr create --base main --head "release/v$NEXT_VER" --title "Release v$NEXT_VER" --fill
```

Change only `NEXT_VER` at the start of the release commands and use the same shell session, or set it again later. Write actual release notes before staging; the quoted filename in `git add` must exist. Stage all other modified files explicitly. If the release branch already exists, update it and its PR instead of creating a second one.

## 4. Review, merge and tag

Before release approval: verify independent review, complete notes, current PR CI and isolated smoke, rollback guidance, image/package contents, and **Radware approval to publish assets publicly** (this GitHub repository is public).

Merge the approved release PR using GitHub's normal authorized process. Then, on a **clean checkout**:

```bash
git switch main
git pull --ff-only origin main
git status --short        # must be empty
cat VERSION               # record approved version
git log -1 --oneline      # record main's release commit
```

Confirm the tag does not already exist; the next command should print **nothing**:

```bash
VER="$(cat VERSION)"
git ls-remote --tags origin "refs/tags/v$VER"
```

**Only after explicit approval**, tag the exact approved main commit:

```bash
git tag -a "v$VER" -m "Container Alert v$VER"
git push origin "v$VER"
```

The tag triggers [Publish Container Alert release](../../.github/workflows/release-package.yml). Its GitHub-hosted runner checks version/ancestry, runs tests, builds the prebuilt image, creates the package and checksum, uploads both assets as a **draft release**, checks the assets, then publishes. PR merges do not automatically create customer packages.

## 5. Verify the published package

Confirm the tagged workflow passed and exactly two assets exist:

```bash
gh run list -R Radware/CyberController-Container-Alert --workflow release-package.yml --limit 5
gh release view "v$VER" -R Radware/CyberController-Container-Alert
```

Download and inspect **without touching Docker**:

```bash
mkdir -p "/tmp/container-alert-check-$VER"
cd "/tmp/container-alert-check-$VER"
gh release download "v$VER" -R Radware/CyberController-Container-Alert
sha256sum --check SHA256SUMS
tar -tzf "container-alert-v$VER.tar.gz" | grep -E 'VERSION|install.sh|watchdog.tar|SHA256SUMS'
mkdir -p extracted
tar -xzf "container-alert-v$VER.tar.gz" -C extracted
(cd extracted/CyberController-Container-Alert && sha256sum --check SHA256SUMS)
cat extracted/CyberController-Container-Alert/VERSION
```

Record the Git tag/commit, CI/smoke URLs, release link, package digest and final image digest. The tagged build may not be byte-identical to the PR build while image bases/dependencies can float; pinning/signing is a separate supply-chain improvement.

If the workflow fails or the release is incomplete, do not distribute it. Inspect the failed workflow/draft and follow the approved correction process; **never rewrite an existing published tag**.

## 6. Customer delivery and rollback

Operators obtain `container-alert-v<VERSION>.tar.gz` and `SHA256SUMS` from the release or approved internal transfer. They verify, extract, and run `bash install.sh`. The archive works equally well on connected and disconnected hosts. It doesn't include active `.env`, `watchdog-config.yaml` or persistent `watchdog/`, so upgrades preserve customer settings and history and recreate **only** the watchdog service.

YAML-only changes require a watchdog restart; `.env`/Compose changes require recreating that service. Python changes require a new tested and published image. Rollback means selecting the last approved image and configuration; follow [Deployment — Rollback](../operations/INSTALLATION_AND_OPERATIONS.md#54-rollback).

## 7. Safety and best practices

- Never run destructive Docker tests on customer, production or shared operational hosts. Use mock Docker unit tests locally and isolated GitHub-hosted Docker acceptance.
- Do not stop or alter CyberController application containers during installation or tests.
- The SHA-256 manifest verifies contents, not the publisher's identity. Only distribute approved release packages.
- Never publish personal lab hostnames, IP addresses, passwords or customer-specific data.
- Require code review and explicit public-distribution approval before tagging. Image builds use prebuilt, versioned artifacts rather than compiling on customer systems.

References: [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow), [Managing Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository), [About releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).


## 8. Developer-only builds and monitoring notes

### Local image build (never a customer installation)

For authorized development:

```bash
export WATCHDOG_VERSION="$(cat VERSION)"
docker compose -f docker-compose.build.yaml build
```

The Dockerfile packages `watchdog.py`, `lifecycle.py`, and `ha_monitor.py`
inside the image. A restart alone does **not** apply Python source edits.
Production deployment requires a published, prebuilt release package.

### Narrow CyberController event exceptions


The intended MariaDB HA syntax-check helper carries the explicit label `com.radware.cybercontroller.role=mariadb-ha-syntax-check`. The configured `ignored_container_events` policy ignores only the designated temporary helper `die` event; normal service container failures remain monitored.

A narrow compatibility fallback exists for dynamic MariaDB dump helpers: it applies only when the helper image, non-primary container name, Docker `AutoRemove` status, expected SQL dump file mount and exit status 137 **all** match. This is not a general rule to suppress MariaDB failures. Any change to monitoring exceptions belongs to Radware R&D/authorized operations, not routine customer alert handling.


These exceptions are not blanket MariaDB or OOM alert suppression. Changes
to monitoring exceptions require R&D review and regression validation.

### Historical design and validation

The [HA design](HA_MONITORING_DESIGN.md) and [dated HA snapshot](HA_VALIDATION_2026-10-13.md)
preserve earlier evidence. The [release acceptance](RELEASE_ACCEPTANCE_V1.6.0.md)
and [earlier failures](RELEASE_VALIDATION_V1.6.0.md) are historical reports,
not current installation instructions or release approval. Use the
[release checklist](RELEASE_V1.6.0_CHECKLIST.md) and actual GitHub checks.
