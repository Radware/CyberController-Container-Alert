"""Regression coverage for planned Docker stops and real termination failures."""

import threading
import unittest
from types import SimpleNamespace

from lifecycle import DockerLifecycleCorrelator
import watchdog


CID = "a" * 64
OTHER_ID = "b" * 64


class LifecycleCorrelatorTests(unittest.TestCase):
    def test_forced_docker_stop_die_before_stop(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("kill", CID, signal="15", now=100)
        tracker.record("kill", CID, signal="9", now=110)
        tracker.record("die", CID, name="svc", exit_code=137, now=110.01)
        self.assertEqual(tracker.drain_ready(now=110.02), [])
        tracker.record("stop", CID, now=110.05)
        [death] = tracker.drain_ready(now=110.06)
        self.assertTrue(death.stopped)
        self.assertEqual(death.signals, ("15", "9"))
        self.assertEqual(death.exit_code, 137)

    def test_stop_before_die_is_correlated(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("stop", CID, now=200)
        tracker.record("die", CID, name="svc", exit_code=143, now=200.1)
        [death] = tracker.drain_ready(now=200.2)
        self.assertTrue(death.stopped)

    def test_kill_without_stop_cannot_suppress(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("kill", CID, signal="9", now=100)
        tracker.record("die", CID, name="svc", exit_code=137, now=100.1)
        tracker.record("stop", OTHER_ID, now=100.2)
        self.assertEqual(tracker.drain_ready(now=102), [])
        [death] = tracker.drain_ready(now=103.1)
        self.assertFalse(death.stopped)
        self.assertEqual(death.signals, ("9",))

    def test_late_stop_cannot_suppress_timeout(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=137, now=100)
        tracker.record("stop", CID, now=105)
        [death] = tracker.drain_ready(now=105)
        self.assertFalse(death.stopped)

    def test_restart_clears_stale_stop_evidence(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("stop", CID, now=100)
        tracker.record("start", CID, now=100.1)
        tracker.record("die", CID, name="svc", exit_code=1, now=100.2)
        [death] = tracker.drain_ready(now=104)
        self.assertFalse(death.stopped)

    def test_oom_after_die_marks_pending(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=137, now=100)
        self.assertTrue(tracker.record("oom", CID, now=100.1))
        [death] = tracker.drain_ready(now=100.1)
        self.assertTrue(death.oom)

    def test_unmatched_oom_returns_false(self):
        tracker = DockerLifecycleCorrelator(3)
        self.assertFalse(tracker.record("oom", CID, now=100))


class DummyContainer:
    def __init__(self, name="svc", cid=CID):
        self.name = name
        self.id = cid
        self.labels = {}
        self.attrs = {
            "Config": {"Image": "test/svc:latest", "Labels": {}},
            "HostConfig": {"AutoRemove": False},
            "State": {"OOMKilled": False},
            "Mounts": [],
        }
        self.image = SimpleNamespace(tags=["test/svc:latest"])

    def logs(self, **kwargs):
        return b"test container log"

    def stats(self, **kwargs):
        return {"memory_stats": {}}


class WatchdogLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.container = DummyContainer()
        w = object.__new__(watchdog.Watchdog)
        w.cfg = {"alert_on_recovery": True}
        w.host = "lab"
        w.runbook_base = "https://example.invalid/runbook"
        w._excluded = set()
        w._ignored_events = []
        w._discovered_checks = {}
        w._oom_containers = set()
        w._lifecycle_enabled = True
        w._lifecycle = DockerLifecycleCorrelator(3)
        w._lifecycle_wake = threading.Event()
        w._lifecycle_cfg = {
            "notify_on_docker_stop": False,
            "log_forced_stop_warning": True,
        }
        w.state = watchdog.WatchdogState("")
        w._cooldown_minutes = 5
        w._boot_time = 0
        w._unhealthy_threshold = 3
        self.alerts = []
        w._dispatch = self.alerts.append
        w.client = SimpleNamespace(containers=SimpleNamespace(get=lambda cid: self.container))
        w._expected_running = set()
        w._expected_down_since = {}
        w._expected_running_grace = 120
        self.w = w

    def event(self, action, *, exit_code=None, signal=None, cid=CID):
        attrs = {"name": "svc"}
        if exit_code is not None:
            attrs["exitCode"] = str(exit_code)
        if signal is not None:
            attrs["signal"] = str(signal)
        return {"Action": action, "Actor": {"ID": cid, "Attributes": attrs}}

    def resolve(self, *, force=False):
        for death in self.w._lifecycle.drain_ready(force=force):
            self.w._resolve_lifecycle_death(death)

    def test_exact_october_incident_planned_sigkill_no_critical(self):
        self.w._handle_event(self.event("kill", signal=15))
        self.w._handle_event(self.event("kill", signal=9))
        self.w._handle_event(self.event("die", exit_code=137))
        self.assertEqual(self.alerts, [])
        self.w._handle_event(self.event("stop"))
        self.resolve()
        self.assertEqual(self.alerts, [])
        self.assertEqual(self.w.state.get("svc").alerted_for, "")

    def test_requested_stop_notification_is_info_only(self):
        self.w._lifecycle_cfg["notify_on_docker_stop"] = True
        self.w._handle_event(self.event("die", exit_code=137))
        self.w._handle_event(self.event("stop"))
        self.resolve()
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0].severity, "INFO")
        self.assertEqual(self.alerts[0].failure_type, "docker-stop")
        self.assertEqual(self.w.state.get("svc").alerted_for, "")

    def test_direct_docker_kill_is_critical(self):
        self.w._handle_event(self.event("kill", signal=9))
        self.w._handle_event(self.event("die", exit_code=137))
        self.resolve(force=True)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0].failure_type, "crashed")
        self.assertEqual(self.alerts[0].severity, "CRITICAL")
        self.assertIn("OOM", self.alerts[0].recommended_action)

    def test_unexpected_nonzero_exit_is_critical(self):
        self.w._handle_event(self.event("die", exit_code=1))
        self.resolve(force=True)
        self.assertEqual([a.failure_type for a in self.alerts], ["crashed"])

    def test_exit_zero_no_alert(self):
        self.w._handle_event(self.event("die", exit_code=0))
        self.resolve(force=True)
        self.assertEqual(self.alerts, [])

    def test_oom_before_die_only_oom(self):
        self.w._handle_event(self.event("oom"))
        self.w._handle_event(self.event("die", exit_code=137))
        self.resolve(force=True)
        self.assertEqual([a.failure_type for a in self.alerts], ["oom"])

    def test_oom_after_die_only_oom_and_no_stale_marker(self):
        self.w._handle_event(self.event("die", exit_code=137))
        self.w._handle_event(self.event("oom"))
        self.resolve()
        self.assertEqual([a.failure_type for a in self.alerts], ["oom"])
        self.assertEqual(self.w._oom_containers, set())

    def test_established_restart_loop_suppresses_die(self):
        self.w.state.get("svc").alerted_for = "restart-loop"
        self.w._handle_event(self.event("die", exit_code=1))
        self.resolve(force=True)
        self.assertEqual(self.alerts, [])

    def test_expected_running_alerts_after_grace(self):
        self.w._expected_running = {"svc"}
        self.w._check_expected_running({"svc": "exited"}, now=100)
        self.w._check_expected_running({"svc": "exited"}, now=219)
        self.assertEqual(self.alerts, [])
        self.w._check_expected_running({"svc": "exited"}, now=220)
        self.assertEqual([a.failure_type for a in self.alerts], ["not-running"])
        self.w._check_expected_running({"svc": "exited"}, now=250)
        self.assertEqual(len(self.alerts), 1)
        self.w._check_expected_running({"svc": "running"}, now=251)
        self.w._maybe_alert_recovery(self.container, self.w.state.get("svc"))
        self.assertEqual([a.failure_type for a in self.alerts],
                         ["not-running", "recovered"])

    def test_expected_running_missing_container_alerts(self):
        self.w._expected_running = {"svc"}
        self.w._check_expected_running({}, now=100)
        self.w._check_expected_running({}, now=230)
        self.assertEqual(self.alerts[0].failure_type, "not-running")
        self.assertIn("missing", self.alerts[0].probe_detail)

    def test_expected_running_does_not_duplicate_open_crash(self):
        self.w._expected_running = {"svc"}
        self.w.state.get("svc").alerted_for = "crashed"
        self.w._check_expected_running({"svc": "exited"}, now=100)
        self.w._check_expected_running({"svc": "exited"}, now=230)
        self.assertEqual(self.alerts, [])


if __name__ == "__main__":
    unittest.main()
