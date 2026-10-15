# Release Smoke Tests — Mandatory Isolation

**Scope:** Container Alert v1.6.0 release validation. The 15 core cases and a separate true kernel-OOM/manual-restart test use real Docker events but must never access an operational or shared Docker Engine.

## Safety rule

**NEVER run `tests/release-smoke.sh` on CyberController, its HA nodes, m920q, a customer system, or another shared machine.** Do not try to make a live host "safe" by changing its watchdog exclusions, switching off its notifications, labelling dummy containers, or setting a live-host opt-in flag.

Instead run the workflow [Release smoke (isolated)](../.github/workflows/release-smoke.yml). It executes on a **GitHub-hosted disposable runner** and creates a second, empty, short-lived, uniquely labelled Docker-in-Docker Engine. Only that Engine's Docker socket is mounted inside the test observer. The temporary outer Engine is visible to the GitHub runner's Docker daemon, never to CyberController or m920q. All test-created resources are disposable and contained within that hosted worker.

The workflow has no corporate customer credentials, no HA exporter dependency, no CyberController Docker socket, no external notification destinations, and no network access from the disposable failure fixtures.

## Launch

The hosted smoke workflow triggers on in-repository release PR changes targeting
`main`. The first run can occur before the workflow is merged into the
default branch. A manual `workflow_dispatch` rerun is available once the
workflow file exists on the default branch.

1. Review [release PR #9](https://github.com/Radware/CyberController-Container-Alert/pull/9) and select its current `release/v1.6.0` branch.
2. For the current PR, verify the automatically triggered **Release smoke (isolated)**
   check. After the workflow is on `main`, an authorized maintainer can
   also manually rerun it from Actions or use:

   ```bash
   gh workflow run release-smoke.yml --ref release/v1.6.0
   ```

3. Confirm the workflow's commit SHA matches the release PR head. It builds the watchdog image on the **hosted runner only**, loads it into a freshly created nested Docker Engine, and checks that the image files match the checked-out source.
4. Download the attached structured evidence artifact (Docker events, alerts, raw UDP receiver packets, state snapshot, run reports). The workflow runs both the full acceptance suite and the additional kernel OOM/manual restart scenario.
5. All cases must PASS; the final cleanup must report zero test-labelled containers and volumes. Keep the actual workflow URL/commit in the release approval record.

## Automatic fail-closed checks

Before even inspecting an image or creating a test resource, the smoke runner requires all of the following:

- GitHub Actions execution on a GitHub-hosted runner (`GITHUB_ACTIONS=true` and `SMOKE_RUNNER_KIND=github-hosted`).
- An explicit localhost-only TCP Docker Engine endpoint `DOCKER_HOST=tcp://127.0.0.1:<ephemeral-port>`; no `DOCKER_CONTEXT` override.
- A unique run marker matching the Docker **daemon-side** label `com.radware.container-alert.smoke-engine`.
- **Zero existing containers** on the target Docker Engine.

The runner has **no live-host override**. If preflight fails it must exit FAIL **without calling Docker to create/inspect any application containers, and without performing cleanup commands against the unverified Engine**.

Within the verified Engine, each fixture uses unique names and a per-run ownership label. Cleanup re-verifies ownership before removal and fails if a command fails or a container/volume is left over. The fast-removal test verifies real exit codes and actual Docker `die` events, not only observer liveness.

## Acceptance criteria

| Scenario | Expected |
|---|---|
| Clean exit | Exit 0 → no alert |
| Nonzero crash | One CRITICAL CRASH |
| Planned forced stop | Docker-requested stop/137 → no false CRASH or OOM |
| Direct SIGKILL | CRITICAL CRASH, not kernel OOM |
| Genuine cgroup OOM | `OOMKilled=true`, exactly one CRITICAL OOM |
| Restart loop | One CRASH → one HIGH RESTART-LOOP; 5 actual restarts |
| Brief or interrupted recovery | No early RECOVERED; actual new failure resets stability |
| Stable recovery | Exactly one INFO RECOVERED after configured uptime |
| Manual restart after crash | Policy `no`, no premature RECOVERED |
| Manual restart after real OOM | Policy `no`, actual kernel OOM and 120-second stability gate |
| Container identity reuse | New Docker ID with old name does not inherit old state |
| Rapid auto-removal | All short-lived test fixtures actually exited, no listener crash or warning flood |
| Native Docker HEALTHCHECK | HIGH UNHEALTHY → INFO RECOVERED |
| Syslog | Locally generated structured alerts received exactly once as real UDP Syslog |
| Cleanup | No leftover test-labelled containers/volumes, no unauthorized modifications |

## Important historical correction

An earlier 2026-10-14 validation ran a nested Engine on **m920q**. Its outer `ca-v160-isolated-engine` container was monitored by the existing `watchdog:1.5.4` and generated HIGH UNHEALTHY and CRITICAL OOM alerts on the **normal SMTP channel**. That test Engine was subsequently removed, but its existence demonstrates that nesting alone is **not production isolation**. Do not repeat that method.

Earlier acceptance JSON and reports remain in `docs/` as traceability, not authorization to rerun those procedures on operational Docker hosts.

## Operator verification (read-only only)

On an installed system, it is acceptable for authorized personnel to review the watchdog's own health, log records and configured alert destinations. It is **not** acceptable to intentionally kill/restart a service to "test notifications." Real failure injection occurs exclusively through the disposable hosted workflow.
