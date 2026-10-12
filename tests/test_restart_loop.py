"""Regressions for Docker auto-restart failures, loop escalation and recovery."""
import threading
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import watchdog


def started_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class FakeContainer:
    name = "test-restart-service"
    id = "abc123def"
    def __init__(self, policy="always", count=0, uptime=3):
        self.status = "running"
        self.attrs = {
            "HostConfig": {"RestartPolicy": {"Name": policy}},
            "State": {"Status": "running", "StartedAt": started_ago(uptime)},
            "RestartCount": count,
            "NetworkSettings": {"Networks": {}},
        }
    def reload(self):
        pass
    def logs(self, **kwargs):
        return b"test error in dependency"


def make_watchdog(container, threshold=3, stable=120):
    wd = object.__new__(watchdog.Watchdog)
    wd.cfg = {"restart_recovery_stability_seconds": stable, "alert_on_recovery": True}
    wd.state = watchdog.WatchdogState("")
    wd.host = "test-host"
    wd.runbook_base = ""
    wd._excluded = set()
    wd._restart_threshold = threshold
    wd._restart_window_secs = 600
    wd._restart_recovery_stability_secs = stable
    wd._unhealthy_threshold = 3
    wd._cooldown_minutes = 5
    wd._ignored_events = []
    wd._discovered_checks = {}
    wd._oom_containers = set()
    wd._auto_cfg = {"enabled": False}
    wd._manual_checks = {}
    wd._boot_time = 1
    wd._grace_until = 0
    wd._folded = []
    wd._folded_lock = threading.Lock()
    wd.client = SimpleNamespace(containers=SimpleNamespace(
        get=lambda name: container, list=lambda all=True: [container]))
    alerts = []
    wd._dispatch = alerts.append
    return wd, alerts


def die(wd, code=2):
    wd._handle_event({"Action": "die", "Actor": {"Attributes": {
        "name": "test-restart-service", "exitCode": str(code)}}})


class RestartLoopRegressionTests(unittest.TestCase):
    def test_event_stream_raises_loop_at_threshold_without_waiting_for_poll(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        die(wd)
        die(wd)
        die(wd)
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop"])

    def test_event_stream_does_not_repeat_crash_after_loop_alert(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(7):
            die(wd)
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop"])

    def test_running_between_crashes_is_not_recovered(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container)
        die(wd)
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_loop_stays_open_during_short_successful_run(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])
        self.assertEqual(wd.state.get(container.name).alerted_for, "restart-loop")

    def test_after_sustained_recovery_send_once_and_reset_loop_counters(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container, stable=120)
        for _ in range(3):
            die(wd)
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop", "recovered"])
        self.assertFalse(wd.state.get(container.name).restart_times)

    def test_healthy_event_cannot_bypass_restart_stability(self):
        container = FakeContainer(uptime=2)
        wd, alerts = make_watchdog(container)
        die(wd)
        wd._handle_event({"Action": "health_status: healthy",
                          "Actor": {"Attributes": {"name": container.name}}})
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_single_auto_restart_recovers_only_after_stability(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container, stable=120)
        die(wd)
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "recovered"])

    def test_no_restart_policy_keeps_existing_immediate_recovery(self):
        container = FakeContainer(policy="no", uptime=3)
        wd, alerts = make_watchdog(container)
        die(wd)
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "recovered"])

    def test_poll_detects_lost_events_from_docker_restart_count(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container)
        wd._poll_all_containers()
        container.attrs["RestartCount"] = 3
        wd._poll_all_containers()
        self.assertIn("restart-loop", [a.failure_type for a in alerts])

    def test_oom_restart_loop_does_not_alternate_oom_and_loop_alerts(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(6):
            wd._handle_event({"Action": "oom", "Actor": {"Attributes": {
                "name": container.name}}})
            die(wd, code=137)
        self.assertEqual([a.failure_type for a in alerts], ["oom", "restart-loop"])
        self.assertFalse(wd._oom_containers)

    def test_unhealthy_events_do_not_replace_open_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        for _ in range(4):
            wd._handle_event({"Action": "health_status: unhealthy",
                              "Actor": {"Attributes": {"name": container.name}}})
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_native_unhealthy_polls_do_not_replace_open_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        container.attrs["State"]["Health"] = {"Status": "unhealthy"}
        for _ in range(4):
            wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_changing_restart_policy_does_not_falsely_recover_active_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, stable=120)
        for _ in range(3):
            die(wd)
        container.attrs["HostConfig"]["RestartPolicy"]["Name"] = "no"
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop", "recovered"])

    def test_recreated_container_name_starts_fresh_restart_history(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, threshold=3)
        die(wd)
        die(wd)
        container.id = "new-container-id"
        container.attrs["RestartCount"] = 0
        die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "crashed"])
        self.assertEqual(len(wd.state.get(container.name).restart_times), 1)
        self.assertEqual(len(wd.state.get(container.name).pending_event_times), 1)

    def test_recovery_disabled_clears_loop_state_after_stability(self):
        container = FakeContainer(uptime=1)
        wd, alerts = make_watchdog(container)
        wd.cfg["alert_on_recovery"] = False
        for _ in range(3):
            die(wd)
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        self.assertEqual(wd.state.get(container.name).alerted_for, "")
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_manual_clean_stop_with_restart_policy_is_not_a_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(5):
            die(wd, code=0)
        wd._poll_all_containers()  # Docker RestartCount remains unchanged.
        self.assertEqual(alerts, [])

    def test_clean_exit_loop_detected_when_docker_restart_count_advances(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container)
        wd._poll_all_containers()
        for _ in range(3):
            die(wd, code=0)
        container.attrs["RestartCount"] = 3
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["restart-loop"])

    def test_manual_http_probe_does_not_overwrite_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        wd._manual_checks[container.name] = {"type": "http", "internal_port": 8080}
        with patch.object(wd, "_manual_health_check", return_value=(False, "HTTP probe failed - 503")):
            for _ in range(4):
                wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])
        self.assertEqual(wd.state.get(container.name).alerted_for, "restart-loop")

    def test_manual_exec_probe_does_not_overwrite_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        wd._manual_checks[container.name] = {"type": "exec", "command": ["false"]}
        with patch.object(wd, "_manual_health_check", return_value=(False, "Exec probe failed")):
            for _ in range(4):
                wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_auto_discovered_probe_does_not_overwrite_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        wd._auto_cfg = {"enabled": True}
        with patch.object(wd, "_auto_discover_health_check", return_value=(False, "HTTP probe failed - 503")):
            for _ in range(4):
                wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_mixed_event_and_poll_missing_restarts_escalate(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        die(wd)
        die(wd)
        # First polling baseline was established after two event-observed failures.
        container.attrs["RestartCount"] = 2
        wd._poll_all_containers()
        # Event stream has missed three further Docker auto-restarts.
        container.attrs["RestartCount"] = 5
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_mixed_event_and_poll_overlap_does_not_double_count(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()  # establish count=0 baseline
        die(wd)
        die(wd)
        container.attrs["RestartCount"] = 3  # 2 event-observed + 1 missed
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])
        self.assertEqual(len(wd.state.get(container.name).restart_times) +
                         len(wd.state.get(container.name).poll_restart_times), 3)
        # Second interval: one event seen and one event missed = 5 total.
        die(wd)
        container.attrs["RestartCount"] = 5
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_restart_loop_emits_once_even_after_cooldown(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, threshold=3)
        for _ in range(3):
            die(wd)
        state = wd.state.get(container.name)
        state.last_alert_time = time.time() - 1000
        for _ in range(5):
            die(wd)
            wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_recreated_container_must_raise_new_crash_incident(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, threshold=5)
        die(wd)
        container.id = "replacement-unique-id"
        container.attrs["RestartCount"] = 0
        die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "crashed"])

    def test_recreated_container_does_not_inherit_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, threshold=3)
        for _ in range(3):
            die(wd)
        container.id = "replacement-unique-id"
        container.attrs["RestartCount"] = 0
        die(wd)
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop", "crashed"])
        self.assertEqual(wd.state.get(container.name).alerted_for, "crashed")

    def test_die_before_restartcount_advances_is_matched_on_later_poll(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        die(wd)
        wd._poll_all_containers()  # Docker's RestartCount is still 0 at this point
        self.assertEqual(len(wd.state.get(container.name).pending_event_times), 1)
        container.attrs["RestartCount"] = 1
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).restart_times), 1)
        self.assertEqual(len(wd.state.get(container.name).poll_restart_times), 0)
        self.assertEqual(len(wd.state.get(container.name).pending_event_times), 0)
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_first_poll_after_die_before_restartcount_advance_keeps_pending(self):
        container = FakeContainer(count=0)
        wd, _ = make_watchdog(container, threshold=5)
        die(wd)
        wd._poll_all_containers()  # baseline=0 while event's start is still pending
        self.assertEqual(len(wd.state.get(container.name).pending_event_times), 1)
        container.attrs["RestartCount"] = 1
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).poll_restart_times), 0)

    def test_old_instance_event_id_cannot_crash_replacement(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        die(wd)
        container.id = "new-unique-id"
        wd._poll_all_containers()
        wd._handle_event({"Action": "die", "Actor": {
            "ID": "abc123def", "Attributes": {
                "name": container.name, "exitCode": "17"}}})
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])
        self.assertEqual(wd.state.get(container.name).last_container_id, "new-unique-id")

    def test_poll_clears_stale_incident_for_replacement_instance(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        for _ in range(3):
            die(wd)
        container.id = "new-unique-id"
        container.attrs["RestartCount"] = 0
        wd._poll_all_containers()
        self.assertEqual(wd.state.get(container.name).alerted_for, "")
        die(wd)
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop", "crashed"])

    def test_persisted_container_identity_prevents_stale_alert_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/state.json"
            source = watchdog.WatchdogState(path)
            old = source.get("test-restart-service")
            old.last_container_id = "old-id"
            old.alerted_for = "restart-loop"
            old.last_alert_time = time.time()
            source.save(123)
            with open(path) as f:
                entry = json.load(f)["containers"]["test-restart-service"]
            self.assertEqual(entry["container_id"], "old-id")
            replacement = FakeContainer()
            replacement.id = "replacement-id"
            wd, alerts = make_watchdog(replacement)
            wd._boot_time = 123  # persisted baseline was saved on the same boot
            wd.state = watchdog.WatchdogState(path)
            wd._restore_state()
            self.assertEqual(wd.state.get(replacement.name).last_container_id, "old-id")
            die(wd)
            self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_event_and_poll_counts_are_not_double_counted(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        die(wd)
        die(wd)
        container.attrs["RestartCount"] = 2
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])


if __name__ == "__main__":
    unittest.main()
