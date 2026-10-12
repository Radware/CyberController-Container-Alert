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
        self.assertEqual(monitor.client.containers.get.call_count, 110)
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


if __name__ == "__main__":
    unittest.main()
