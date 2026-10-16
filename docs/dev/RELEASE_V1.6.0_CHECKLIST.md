# Container Alert v1.6.0 — Final Release Checklist

**This checklist applies only to v1.6.0.** For every subsequent release, use the permanent [Development and Release Guide](DEVELOPMENT_AND_RELEASE.md) and create a corresponding version-specific approval checklist only if needed.

**Status on October 16, 2026:** `main` still contains v1.5.4; `release/v1.6.0` contains `VERSION=1.6.0` and is reviewed through [PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9), which is open/unmerged. The v1.6.0 GitHub Release has **not** been published by this work.

## Approval and verification — before merge

- [ ] Final engineering review of PR #9: alert/incident lifecycle, notifications, HA behavior, security and image packaging.
- [ ] Normal GitHub CI **and** [Release smoke (isolated)](RELEASE_SMOKE_TESTING.md) both pass against the **latest PR head SHA**. The smoke is a disposable GitHub-hosted test; never run it against an operational Docker host.
- [ ] Document all changes since v1.5.4 in [release notes](../customer/RELEASE_NOTES_V1.6.0.md). Verify the README history includes `1.6.0`.
- [ ] Confirm exact supported customer message text, severity mappings, and downstream Syslog/SNMP compatibility.
- [ ] Confirm there are no private lab hostnames, IP addresses, personal information or credentials in the **published installer archive**, documentation and release notes.
- [ ] Approve distributing the Docker image and complete release package through this **public** GitHub repository.
- [ ] Confirm upgrade/rollback procedure and identify any additional authorized HA/customer-delivery verification needed. CI success alone does not establish real customer SMTP/Slack/SNMP delivery.

## Finalize and publish

After **all** approvals above:

1. Merge PR #9 to `main` through the reviewed GitHub process.
2. In a clean checkout, run:

   ```bash
   git switch main
   git pull --ff-only origin main
   cat VERSION                 # expect 1.6.0
   git status --short          # expect empty
   git ls-remote --tags origin refs/tags/v1.6.0
   ```

   The final command must show **no existing tag**. If it exists, investigate; don't overwrite tags.

3. With approval to publish, tag the approved `main` commit:

   ```bash
   test "$(cat VERSION)" = "1.6.0" || exit 1
   git tag -a v1.6.0 -m "Container Alert v1.6.0"
   git push origin v1.6.0
   ```

4. Confirm the tagged [release workflow](../../.github/workflows/release-package.yml) passed:

   ```bash
   gh run list -R Radware/CyberController-Container-Alert --workflow release-package.yml --limit 5
   gh release view v1.6.0 -R Radware/CyberController-Container-Alert
   ```

5. Verify the **two published assets**, `container-alert-v1.6.0.tar.gz` and `SHA256SUMS`, without installing or starting containers:

   ```bash
   mkdir -p /tmp/container-alert-v1.6.0-verify
   cd /tmp/container-alert-v1.6.0-verify
   gh release download v1.6.0 -R Radware/CyberController-Container-Alert
   sha256sum --check SHA256SUMS
   mkdir -p unpacked
   tar -xzf container-alert-v1.6.0.tar.gz -C unpacked
   (cd unpacked/CyberController-Container-Alert && sha256sum --check SHA256SUMS)
   cat unpacked/CyberController-Container-Alert/VERSION
   ```

6. Record tag, commit SHA, workflow link, package hash and final image digest. Inform operators that the *same* complete package works on connected and disconnected hosts, as described in [Deployment](../customer/INSTALLATION_AND_OPERATIONS.md).

**Stop if a release gate fails.** Don't distribute a draft, incomplete or unapproved release. Do not run installation, Docker failure injection or HA failover tests on production systems as part of this checklist.
