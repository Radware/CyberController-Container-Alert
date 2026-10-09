# Optional HA active-node alert — validation status

**Branch:** feature/optional-ha-failover-alert
**Status:** Implementation complete for review; controlled two-node failover and release DoD **pending**
**Date:** 2026-10-09

## Automated verification

- **25 unit/regression tests pass** on the development workstation, covering HA metric parsing, state validation, transition confirmation, failback, newly-active-node-only sending, persistence/duplicate suppression, bad/unavailable metrics, disabled-by-default behavior, existing Docker monitoring independence, Slack/SMTP/syslog/SNMP dispatch semantics and previous watchdog regressions.
- **24 tests passed** in an isolated Docker container on the HA test appliance using the existing watchdog:1.5.4 runtime image with the new source mounted read-only (prior to adding the extra SMTP test).
- Python syntax and existing shell-script syntax checks pass.
- The local clean Docker build was **not completed** because Docker Hub returned HTTP 504 while fetching the python:3.11-slim base image. New Dockerfile includes both watchdog.py and ha_monitor.py; full clean image build remains for CI.

## Actual appliance checks — read-only

Active side (lab host 10.199.0.241):

- Pacemaker sees two online nodes, 11 HA resource instances; HAOperatorRep-Res promoted on .241.
- Direct HA exporter port 9664 returns HTTP 200. The new parser identifies active=.241, local=.241; one read took approximately **78 ms** in the isolated test container.
- Calling Watchdog._poll_ha() against the **real** exporter twice establishes and saves state format v1 containing ha_monitor.active_node=10.199.0.241, without sending an initial alert.
- No Docker socket, Pacemaker control, configuration change, notification receiver, or running-watchdog deployment was used in the isolated tests.

Standby side (10.199.0.242):

- Node is online and its local HA exporter returns HTTP 200 from localhost:9664.
- No Container Watchdog deployment or watchdog:1.5.4 image was found there during read-only inspection.
- Native Prometheus HA scrape target 9002 vs HA exporter 9664 mismatch remains a separate appliance defect; this feature bypasses Prometheus and uses the existing local exporter directly.

## Outstanding acceptance gates (not claimed complete)

1. Deploy the approved watchdog build and enable ha_monitor **on both HA nodes**, with persistent local state and outbound alert destinations.
2. Verify both nodes learn the same initial active owner *before* the failover.
3. In an approved maintenance window, perform a **supported controlled switchover** and then failback; verify one notification comes only from the newly active node each time, with correct timestamps and old/new node details.
4. Confirm that losing the original active node does not prevent the surviving node from sending the notification; avoid artificial network partitions while STONITH fencing is disabled.
5. Verify at least syslog delivery end-to-end, performance, clean image build/CI, and disabled-option backward compatibility.

**Why no forced failover yet:** Testing a cluster-wide role change before the surviving node has a watchdog would not validate the requested operator notification. Because STONITH is disabled, a null-route or network partition is additionally unsafe (split-brain risk). No cluster state or firewall settings were changed.

## Rollback

Because no live agent was replaced or configured, there is currently nothing to roll back on the HA appliance. When deploying for controlled QA, keep the previous watchdog image/configuration; disable ha_monitor (or roll back to v1.5.4) to revert the feature without touching the HA cluster.
