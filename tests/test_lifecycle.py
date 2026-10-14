"""Integration regression: Docker stop evidence feeds ONE PR #5 incident engine."""
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from lifecycle import DockerLifecycleCorrelator
import watchdog

CID = "a" * 64
OTHER = "b" * 64


class CorrelationTests(unittest.TestCase):
    def test_die_before_stop(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=137, now=10)
        tracker.record("stop", CID, now=10.1)
        [death] = tracker.drain_ready(now=10.2)
        self.assertTrue(death.stopped)

    def test_stop_before_die(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("stop", CID, now=10)
        tracker.record("die", CID, name="svc", exit_code=137, now=10.1)
        [death] = tracker.drain_ready(now=10.2)
        self.assertTrue(death.stopped)

    def test_late_stop_not_suppressed(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=137, now=10)
        tracker.record("stop", CID, now=14)
        [death] = tracker.drain_ready(now=14)
        self.assertFalse(death.stopped)

    def test_wrong_id_not_suppressed(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=137, now=10)
        tracker.record("stop", OTHER, now=10.1)
        [death] = tracker.drain_ready(now=14)
        self.assertFalse(death.stopped)

    def test_start_clears_old_stop(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("stop", CID, now=10)
        tracker.record("start", CID, now=10.1)
        tracker.record("die", CID, name="svc", exit_code=137, now=10.2)
        [death] = tracker.drain_ready(now=14)
        self.assertFalse(death.stopped)

    def test_next_run_stop_cannot_hide_previous_run_crash(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=1, now=10)
        tracker.record("start", CID, now=10.1)
        tracker.record("stop", CID, now=10.2)
        [death] = tracker.drain_ready(now=10.3)
        self.assertFalse(death.stopped)

    def test_single_stop_can_suppress_only_latest_die(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=2, now=10)
        tracker.record("die", CID, name="svc", exit_code=137, now=10.2)
        tracker.record("stop", CID, now=10.3)
        deaths = tracker.drain_ready(force=True)
        self.assertEqual([d.stopped for d in deaths], [False, True])

    def test_oom_after_die_is_correlated(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=137, now=10)
        self.assertTrue(tracker.record("oom", CID, now=10.1))
        [death] = tracker.drain_ready(now=10.2)
        self.assertTrue(death.oom)

    def test_oom_without_pending_die_is_not_matched(self):
        tracker = DockerLifecycleCorrelator(3)
        self.assertFalse(tracker.record("oom", CID, now=10))

    def test_no_premature_resolution(self):
        tracker = DockerLifecycleCorrelator(3)
        tracker.record("die", CID, name="svc", exit_code=1, now=10)
        self.assertEqual(tracker.drain_ready(now=12.99), [])
        self.assertEqual(len(tracker.drain_ready(now=13.01)), 1)


class FakeContainer:
    def __init__(self, cid=CID, name="svc"):
        self.id, self.name = cid, name
        self.status = "running"
        self.labels = {}
        self.attrs = {
            "Config": {"Image": "test/svc", "Labels": {}},
            "HostConfig": {"AutoRemove": False, "RestartPolicy": {"Name": "always"}},
            "State": {"OOMKilled": False,
                      "StartedAt": (datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat()},
            "Mounts": [], "RestartCount": 0,
            "NetworkSettings": {"Networks": {}},
        }
        self.image = SimpleNamespace(tags=["test/svc:latest"])

    def logs(self, **kwargs):
        return b"failure in test container"

    def stats(self, **kwargs):
        return {"memory_stats": {}}

    def reload(self):
        pass


class WatchdogLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.container = FakeContainer()
        wd = object.__new__(watchdog.Watchdog)
        wd.cfg = {"alert_on_recovery": True}
        wd.state = watchdog.WatchdogState("")
        wd.host = "lab"
        wd.runbook_base = ""
        wd._excluded = set()
        wd._ignored_events = []
        wd._discovered_checks = {}
        wd._oom_containers = set()
        wd._missing_events = {}
        wd._lifecycle_enabled = True
        wd._lifecycle = DockerLifecycleCorrelator(3)
        wd._lifecycle_wake = threading.Event()
        wd._restart_threshold = 3
        wd._restart_window_secs = 600
        wd._restart_recovery_stability_secs = 120
        wd._unhealthy_threshold = 3
        wd._cooldown_minutes = 5
        wd._auto_cfg = {"enabled": False}
        wd._manual_checks = {}
        wd._boot_time = 0
        wd._grace_until = 0
        wd._folded = []
        wd._folded_lock = threading.Lock()
        wd.client = SimpleNamespace(
            containers=SimpleNamespace(get=lambda cid: self.container,
                                       list=lambda all=True: [self.container]))
        self.alerts = []
        wd._dispatch = self.alerts.append
        self.wd = wd

    def event(self, action, code=None, cid=CID, timeNano=None):
        attrs = {"name": self.container.name}
        if code is not None:
            attrs["exitCode"] = str(code)
        event = {"Action": action, "Actor": {"ID": cid, "Attributes": attrs}}
        if timeNano:
            event["timeNano"] = timeNano
        return event

    def resolve(self):
        for death in self.wd._lifecycle.drain_ready(force=True):
            self.wd._resolve_lifecycle_death(death)

    def reasons(self):
        return [alert.failure_type for alert in self.alerts]

    def test_october_forced_stop_does_not_crash(self):
        self.wd._handle_event(self.event("die", 137))
        self.wd._handle_event(self.event("stop"))
        self.resolve()
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.wd.state.get("svc").alerted_for, "")

    def test_stop_first_suppresses_forced_exit(self):
        self.wd._handle_event(self.event("stop"))
        self.wd._handle_event(self.event("die", 137))
        self.resolve()
        self.assertEqual(self.reasons(), [])

    def test_direct_kill_without_stop_is_a_crash(self):
        self.wd._handle_event(self.event("die", 137))
        self.resolve()
        self.assertEqual(self.reasons(), ["crashed"])
        self.assertNotIn("likely OOM", self.alerts[0].recommended_action)

    def test_nonzero_exit_without_stop_is_a_crash(self):
        self.wd._handle_event(self.event("die", 2))
        self.resolve()
        self.assertEqual(self.reasons(), ["crashed"])

    def test_clean_exit_is_not_a_crash(self):
        self.wd._handle_event(self.event("die", 0))
        self.resolve()
        self.assertEqual(self.reasons(), [])

    def test_oom_before_die_no_duplicate(self):
        self.wd._handle_event(self.event("oom"))
        self.wd._handle_event(self.event("die", 137))
        self.resolve()
        self.assertEqual(self.reasons(), ["oom"])

    def test_oom_after_die_no_duplicate_or_stale_marker(self):
        self.wd._handle_event(self.event("die", 137))
        self.wd._handle_event(self.event("oom"))
        self.resolve()
        self.assertEqual(self.reasons(), ["oom"])
        self.assertEqual(self.wd._oom_containers, set())

    def test_pending_die_does_not_erase_active_restart_loop(self):
        state = self.wd.state.get("svc")
        state.alerted_for = "restart-loop"
        self.wd._handle_event(self.event("die", 2))
        self.resolve()
        self.assertEqual(self.reasons(), [])
        self.assertEqual(state.alerted_for, "restart-loop")

    def test_multiple_pending_dies_still_one_crash(self):
        for _ in range(4):
            self.wd._handle_event(self.event("die", 2))
        self.resolve()
        self.assertEqual(self.reasons(), ["crashed"])

    def test_pending_death_from_replaced_container_is_ignored(self):
        self.wd._handle_event(self.event("die", 137))
        new = FakeContainer(cid=OTHER)
        self.wd._sync_container_identity(self.wd.state.get("svc"), new)
        self.resolve()
        self.assertEqual(self.reasons(), [])

    def test_delayed_old_event_after_recovery_is_ignored(self):
        state = self.wd.state.get("svc")
        state.last_recovery_time = time.time()
        self.wd._handle_event(self.event("die", 2, timeNano=int((time.time()-5)*1e9)))
        self.resolve()
        self.assertEqual(self.reasons(), [])

    def test_stop_evidence_skips_container_lookup(self):
        self.wd.client.containers.get = Mock(side_effect=AssertionError("unexpected inspect"))
        self.wd._handle_event(self.event("stop"))
        self.wd.client.containers.get.assert_not_called()

    def test_lifecycle_can_be_disabled(self):
        self.wd._lifecycle_enabled = False
        self.wd._handle_event(self.event("die", 2))
        self.assertEqual(self.reasons(), ["crashed"])

    def test_event_subscription_does_not_need_kill(self):
        events = []
        self.wd._stop_event = threading.Event()
        self.wd.client.events = lambda **kw: events.append(kw) or iter([])
        self.wd._event_listener()
        filters = events[0]["filters"]["event"]
        self.assertIn("start", filters)
        self.assertIn("stop", filters)
        self.assertNotIn("kill", filters)


if __name__ == "__main__":
    unittest.main()
