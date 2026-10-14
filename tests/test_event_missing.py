import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import docker
import watchdog


class MissingContainerEventTests(unittest.TestCase):
    def make_subject(self):
        monitor = object.__new__(watchdog.Watchdog)
        monitor._excluded = set()
        monitor._missing_events = {}
        monitor.client = SimpleNamespace(containers=Mock())
        monitor.client.containers.get.side_effect = docker.errors.NotFound("removed")
        return monitor

    def test_flood_logs_once_per_event_type_and_uses_id(self):
        monitor = self.make_subject()
        events = []
        for _ in range(55):
            for action in ("health_status: unhealthy", "die"):
                events.append({"Action": action, "Actor": {"ID": "old-instance-123", "Attributes": {"name": "health-demo", "exitCode": "2"}}})
        with self.assertLogs("watchdog", level="WARNING") as logs:
            for event in events:
                monitor._handle_event(event)
        self.assertEqual(len(logs.output), 2)
        self.assertEqual(monitor.client.containers.get.call_count, 1)
        self.assertTrue(all(call.args[0] == "old-instance-123" for call in monitor.client.containers.get.call_args_list))

    def test_different_instance_not_suppressed(self):
        monitor = self.make_subject()
        with self.assertLogs("watchdog", level="WARNING") as logs:
            for cid in ("old-id", "new-id"):
                monitor._handle_event({"Action": "die", "Actor": {"ID": cid, "Attributes": {"name": "same-name"}}})
        self.assertEqual(len(logs.output), 2)

    def test_legacy_event_falls_back_to_name(self):
        monitor = self.make_subject()
        with self.assertLogs("watchdog", level="WARNING"):
            monitor._handle_event({"Action": "die", "Actor": {"Attributes": {"name": "old-service"}}})
        monitor.client.containers.get.assert_called_once_with("old-service")


    def test_expiry_retries_missing_container(self):
        monitor = self.make_subject()
        event = {"Action": "die", "Actor": {"ID": "expired-id", "Attributes": {"name": "expired"}}}
        with patch("watchdog.time.monotonic", side_effect=[100, 101, 161]):
            with self.assertLogs("watchdog", level="WARNING") as logs:
                for _ in range(3):
                    monitor._handle_event(event)
        self.assertEqual(monitor.client.containers.get.call_count, 2)
        self.assertEqual(len(logs.output), 2)

    def test_cache_bounded_during_high_churn(self):
        monitor = self.make_subject()
        with patch("watchdog.time.monotonic", return_value=100):
            with patch("watchdog.log.warning"):
                for n in range(1030):
                    monitor._handle_event({"Action": "die", "Actor": {
                        "ID": f"old-{n}", "Attributes": {"name": f"old-{n}"}
                    }})
                self.assertEqual(len(monitor._missing_events), 1024)
                self.assertNotIn("old-0", monitor._missing_events)
                monitor._handle_event({"Action": "die", "Actor": {
                    "ID": "old-0", "Attributes": {"name": "old-0"}
                }})
        self.assertEqual(monitor.client.containers.get.call_count, 1031)
        self.assertEqual(len(monitor._missing_events), 1024)

    def test_idless_legacy_name_is_reinspected(self):
        monitor = self.make_subject()
        event = {"Action": "die", "Actor": {"Attributes": {"name": "reusable-name"}}}
        with self.assertLogs("watchdog", level="WARNING") as logs:
            monitor._handle_event(event)
            monitor._handle_event(event)
        self.assertEqual(monitor.client.containers.get.call_count, 2)
        self.assertEqual(len(logs.output), 1)

    def test_rename_and_name_reuse_records_old_container_not_new(self):
        monitor = self.make_subject()
        old = SimpleNamespace(name="service-old", id="old-id", attrs={"Config": {"Labels": {}}})
        monitor.client.containers.get.side_effect = None
        monitor.client.containers.get.return_value = old
        monitor._ignored_events = []
        monitor._discovered_checks = {}
        monitor._oom_containers = set()
        old_state = watchdog.ContainerState("service-old")
        monitor.state = SimpleNamespace(get=Mock(return_value=old_state))
        monitor.host = "test"
        monitor.runbook_base = "test"
        monitor._claim_alert = Mock(return_value=True)
        monitor._dispatch = Mock()
        event = {"Action": "die", "Actor": {"ID": "old-id", "Attributes": {
            "name": "service", "exitCode": "3"
        }}}
        with patch("watchdog.build_payload", return_value=object()):
            monitor._handle_event(event)
        monitor.client.containers.get.assert_called_once_with("old-id")
        monitor.state.get.assert_called_once_with("service-old")
        monitor._claim_alert.assert_called_once_with(old_state, "crashed")
        monitor._dispatch.assert_called_once()



if __name__ == "__main__":
    unittest.main()
