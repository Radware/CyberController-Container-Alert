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
    wd._missing_events = {}  # PR #6 event cache (normal Watchdog init owns it)
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


def die(wd, code=2, occurred_at=None):
    event = {"Action": "die", "Actor": {"Attributes": {
        "name": "test-restart-service", "exitCode": str(code)}}}
    if occurred_at is not None:
        event["timeNano"] = int(occurred_at * 1_000_000_000)
    wd._handle_event(event)


def trigger_loop(wd, container, code=2):
    """Count a real Docker restart-counter delta, not event deliveries."""
    wd._poll_all_containers()  # establish the current counter baseline
    if code is not None:
        die(wd, code)
    container.attrs["RestartCount"] += wd._restart_threshold
    wd._poll_all_containers()


class RestartLoopRegressionTests(unittest.TestCase):
    def test_events_send_initial_crash_but_loop_waits_for_counter_poll(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        wd._poll_all_containers()
        for _ in range(3):
            die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])
        container.attrs["RestartCount"] = 3
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_event_stream_does_not_repeat_crash_after_loop_alert(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        for _ in range(7):
            die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_running_between_crashes_is_not_recovered(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container)
        die(wd)
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_loop_stays_open_during_short_successful_run(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])
        self.assertEqual(wd.state.get(container.name).alerted_for, "restart-loop")

    def test_after_sustained_recovery_send_once_and_reset_loop_counters(self):
        container = FakeContainer(uptime=3)
        wd, alerts = make_watchdog(container, stable=120)
        trigger_loop(wd, container)
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop", "recovered"])
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
        wd._poll_all_containers()
        wd._handle_event({"Action": "oom", "Actor": {"Attributes": {"name": container.name}}})
        die(wd, code=137)
        container.attrs["RestartCount"] = 3
        wd._poll_all_containers()
        for _ in range(5):
            wd._handle_event({"Action": "oom", "Actor": {"Attributes": {"name": container.name}}})
            die(wd, code=137)
        self.assertEqual([a.failure_type for a in alerts], ["oom", "restart-loop"])
        self.assertFalse(wd._oom_containers)

    def test_unhealthy_events_do_not_replace_open_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        for _ in range(4):
            wd._handle_event({"Action": "health_status: unhealthy",
                              "Actor": {"Attributes": {"name": container.name}}})
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_native_unhealthy_polls_do_not_replace_open_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        container.attrs["State"]["Health"] = {"Status": "unhealthy"}
        for _ in range(4):
            wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_changing_restart_policy_does_not_falsely_recover_active_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, stable=120)
        trigger_loop(wd, container)
        container.attrs["HostConfig"]["RestartPolicy"]["Name"] = "no"
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop", "recovered"])

    def test_recreated_container_name_starts_fresh_restart_history(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container, threshold=3)
        wd._poll_all_containers()
        die(wd)
        die(wd)
        container.id = "new-container-id"
        container.attrs["RestartCount"] = 0
        die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "crashed"])
        self.assertEqual(len(wd.state.get(container.name).restart_times), 0)

    def test_recovery_disabled_clears_loop_state_after_stability(self):
        container = FakeContainer(uptime=1)
        wd, alerts = make_watchdog(container)
        wd.cfg["alert_on_recovery"] = False
        trigger_loop(wd, container)
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
        trigger_loop(wd, container)
        wd._manual_checks[container.name] = {"type": "http", "internal_port": 8080}
        with patch.object(wd, "_manual_health_check", return_value=(False, "HTTP probe failed - 503")):
            for _ in range(4):
                wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_manual_exec_probe_does_not_overwrite_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        wd._manual_checks[container.name] = {"type": "exec", "command": ["false"]}
        with patch.object(wd, "_manual_health_check", return_value=(False, "Exec probe failed")):
            for _ in range(4):
                wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_auto_discovered_probe_does_not_overwrite_restart_loop(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        wd._auto_cfg = {"enabled": True}
        with patch.object(wd, "_auto_discover_health_check", return_value=(False, "HTTP probe failed - 503")):
            for _ in range(4):
                wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_counter_detects_restarts_even_with_missing_events(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        die(wd)
        die(wd)
        container.attrs["RestartCount"] = 2
        wd._poll_all_containers()
        container.attrs["RestartCount"] = 5
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])
        self.assertEqual(len(wd.state.get(container.name).restart_times), 5)

    def test_counter_counts_each_restart_once_despite_event_overlap(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        die(wd)
        die(wd)
        container.attrs["RestartCount"] = 3
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])
        self.assertEqual(len(wd.state.get(container.name).restart_times), 3)
        die(wd)
        container.attrs["RestartCount"] = 5
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop"])

    def test_restart_loop_emits_once_even_after_cooldown(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        state = wd.state.get(container.name)
        state.last_alert_time = time.time() - 1000
        for i in range(5):
            die(wd)
            container.attrs["RestartCount"] += 1
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
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        container.id = "replacement-unique-id"
        container.attrs["RestartCount"] = 0
        die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop", "crashed"])
        self.assertEqual(wd.state.get(container.name).alerted_for, "crashed")

    def test_die_before_counter_increment_does_not_count_early(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        die(wd)
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).restart_times), 0)
        container.attrs["RestartCount"] = 1
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).restart_times), 1)
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_initial_zero_counter_baseline_does_not_count_event(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container)
        die(wd)
        wd._poll_all_containers()  # establishes baseline at 0
        self.assertEqual(len(wd.state.get(container.name).restart_times), 0)
        container.attrs["RestartCount"] = 1
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).restart_times), 1)
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

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
        trigger_loop(wd, container)
        container.id = "new-unique-id"
        container.attrs["RestartCount"] = 0
        wd._poll_all_containers()
        self.assertEqual(wd.state.get(container.name).alerted_for, "")
        die(wd)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop", "crashed"])

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

    def test_timestamp_parser_prefers_nanoseconds_then_seconds(self):
        now = time.time()
        self.assertAlmostEqual(
            watchdog.docker_event_timestamp({"timeNano": int(now * 1_000_000_000), "time": 1}),
            now, places=4)
        self.assertEqual(watchdog.docker_event_timestamp({"time": 123}), 123)
        self.assertIsNone(watchdog.docker_event_timestamp({}))

    def test_genuinely_new_event_after_recovery_creates_new_crash(self):
        container = FakeContainer()
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        when = time.time()
        die(wd, occurred_at=when)
        self.assertEqual([a.failure_type for a in alerts],
                         ["crashed", "restart-loop", "recovered", "crashed"])

    def test_counter_before_delayed_events_cannot_duplicate_restarts(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        occurred_at = time.time() - 3
        container.attrs["RestartCount"] = 3
        wd._poll_all_containers()
        for _ in range(2):
            die(wd, occurred_at=occurred_at)
        self.assertEqual(len(wd.state.get(container.name).restart_times), 3)
        self.assertNotIn("restart-loop", [a.failure_type for a in alerts])
        wd._poll_all_containers()
        self.assertNotIn("restart-loop", [a.failure_type for a in alerts])

    def test_nonzero_historical_baseline_does_not_consume_a_new_event(self):
        container = FakeContainer(count=7)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        die(wd)
        wd._poll_all_containers()  # RestartCount still 7
        self.assertFalse(wd.state.get(container.name).restart_times)
        container.attrs["RestartCount"] = 8
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).restart_times), 1)
        self.assertEqual([a.failure_type for a in alerts], ["crashed"])

    def test_delayed_events_older_than_window_are_not_counted(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        for _ in range(5):
            die(wd, occurred_at=time.time() - 700)
        self.assertNotIn("restart-loop", [a.failure_type for a in alerts])
        self.assertFalse(wd.state.get(container.name).restart_times)

    def test_poll_gap_longer_than_window_baselines_unknown_increments(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        state = wd.state.get(container.name)
        state.last_restart_poll_at = time.time() - 700
        container.attrs["RestartCount"] = 9
        wd._poll_all_containers()
        self.assertEqual(len(state.restart_times), 0)
        self.assertNotIn("restart-loop", [a.failure_type for a in alerts])
        container.attrs["RestartCount"] = 10
        wd._poll_all_containers()
        self.assertEqual(len(state.restart_times), 1)

    def test_expired_restart_observations_are_not_counted_after_poll_gap(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container, threshold=5)
        wd._poll_all_containers()
        container.attrs["RestartCount"] = 4
        wd._poll_all_containers()
        state = wd.state.get(container.name)
        state.restart_times = [time.time() - 700] * 4
        state.last_restart_poll_at = time.time() - 700
        container.attrs["RestartCount"] = 5
        wd._poll_all_containers()
        self.assertFalse(state.restart_times)
        self.assertNotIn("restart-loop", [a.failure_type for a in alerts])

    def test_delayed_die_after_recovery_does_not_reopen_incident(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container)
        trigger_loop(wd, container)
        old_failure = time.time() - 150
        container.attrs["State"]["StartedAt"] = started_ago(130)
        wd.state.get(container.name).last_failure_time = time.time() - 130
        wd._poll_all_containers()
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop", "recovered"])
        die(wd, occurred_at=old_failure)
        self.assertEqual([a.failure_type for a in alerts], ["crashed", "restart-loop", "recovered"])
        self.assertEqual(wd.state.get(container.name).alerted_for, "")

    def test_counter_rollover_restarts_new_baseline(self):
        container = FakeContainer(count=0)
        wd, alerts = make_watchdog(container)
        wd._poll_all_containers()
        container.attrs["RestartCount"] = 2
        wd._poll_all_containers()
        container.attrs["RestartCount"] = 0
        wd._poll_all_containers()
        self.assertFalse(wd.state.get(container.name).restart_times)
        container.attrs["RestartCount"] = 1
        wd._poll_all_containers()
        self.assertEqual(len(wd.state.get(container.name).restart_times), 1)
        self.assertNotIn("restart-loop", [a.failure_type for a in alerts])

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
