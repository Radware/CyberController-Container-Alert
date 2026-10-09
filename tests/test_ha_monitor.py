"""Opt-in HA monitor tests. No live cluster, Docker daemon or network required."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import watchdog
from ha_monitor import (
    HAObservation, HARoleTracker, MAX_METRICS_BYTES,
    parse_ha_metrics, read_ha_observation,
)

A = "10.199.0.241"
B = "10.199.0.242"


def metrics(owner=A, local=A, *, pacemaker=1, quorum=1, extras=""):
    # Deliberately put labels in a different order from the live exporter.
    return f"""
# HELP ha_cluster_pacemaker_resources Cluster role state
ha_cluster_scrape_success{{collector="corosync"}} 1
ha_cluster_scrape_success{{collector="pacemaker"}} {pacemaker}
ha_cluster_corosync_quorate {quorum}
ha_cluster_corosync_member_votes{{node="{local}",local="true",node_id="1"}} 1
ha_cluster_corosync_member_votes{{node="{B if local == A else A}",local="false",node_id="2"}} 1
ha_cluster_pacemaker_resources{{node="{owner}",role="promoted",resource="HAOperatorRep-Res",status="active"}} 1
ha_cluster_pacemaker_resources{{node="{B if owner == A else A}",role="unpromoted",resource="HAOperatorRep-Res",status="active"}} 1
ha_cluster_pacemaker_resources{{node="{owner}",role="promoted",resource="MariaDbRep-Res",status="active"}} 1
{extras}
"""


class FakeResponse:
    def __init__(self, content: bytes, status=200):
        self.content = content
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size=16384):
        yield self.content

    def close(self):
        self.closed = True


class HAReaderTests(unittest.TestCase):
    def test_parse_realistic_exporter_snapshot(self):
        self.assertEqual(parse_ha_metrics(metrics(owner=A, local=B)),
                         HAObservation(active_node=A, local_node=B))

    def test_fail_closed_on_invalid_state(self):
        cases = [
            metrics(pacemaker=0), metrics(quorum=0),
            metrics().replace('collector="pacemaker"', 'collector="unknown"'),
            metrics().replace('local="true"', 'local="false"'),
            metrics().replace('resource="HAOperatorRep-Res"', 'resource="Failback-Res"'),
            metrics(extras=f'ha_cluster_pacemaker_resources{{node="{B}",role="promoted",resource="HAOperatorRep-Res",status="active"}} 1'),
            metrics(extras=f'ha_cluster_corosync_member_votes{{node="{B}",local="true"}} 1'),
            metrics(extras='ha_cluster_corosync_quorate 1'),
            metrics().replace('ha_cluster_corosync_quorate 1', 'ha_cluster_corosync_quorate NaN'),
            metrics().replace('ha_cluster_corosync_quorate 1', 'ha_cluster_corosync_quorate not-a-number'),
            "",
        ]
        for sample in cases:
            with self.subTest(sample=sample[-75:]), self.assertRaises(ValueError):
                parse_ha_metrics(sample)

    def test_ignores_non_matching_roles_and_untrusted_other_metric(self):
        sample = metrics(owner=A, local=A, extras=
            'ha_cluster_pacemaker_resources{node="x",resource="HAOperatorRep-Res",role="promoted",status="active"} 0\n'
            'other_metric{malformed} 9999')
        self.assertEqual(parse_ha_metrics(sample).active_node, A)

    def test_http_is_local_bounded_and_closed(self):
        response = FakeResponse(metrics().encode())
        session = Mock(get=Mock(return_value=response))
        result = read_ha_observation(session, "http://127.0.0.1:9664/metrics")
        self.assertEqual(result.active_node, A)
        self.assertTrue(response.closed)
        session.get.assert_called_once_with(
            "http://127.0.0.1:9664/metrics", timeout=3, stream=True, allow_redirects=False)

    def test_rejects_remote_or_wrong_path_endpoints(self):
        for url in ("http://10.199.0.242:9664/metrics",
                    "http://localhost:9664/admin", "https://localhost:9664/metrics",
                    "http://localhost:9664/metrics?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                read_ha_observation(Mock(), url)

    def test_http_error_oversized_response_or_connection_failure(self):
        for response in (FakeResponse(b"", status=500),
                         FakeResponse(b"X" * (MAX_METRICS_BYTES + 1))):
            session = Mock(get=Mock(return_value=response))
            with self.assertRaises(ValueError):
                read_ha_observation(session, "http://localhost:9664/metrics")
            self.assertTrue(response.closed)
        session = Mock(get=Mock(side_effect=TimeoutError("timeout")))
        with self.assertRaises(TimeoutError):
            read_ha_observation(session, "http://localhost:9664/metrics")


class HATrackerTests(unittest.TestCase):
    def test_initial_baseline_and_stable_state_are_silent(self):
        tracker = HARoleTracker()
        self.assertEqual(tracker.observe(HAObservation(A, A)), (True, None))
        self.assertEqual(tracker.observe(HAObservation(A, A)), (False, None))

    def test_change_requires_two_good_polls_and_sends_only_from_new_owner(self):
        for local in (A, B):
            tracker = HARoleTracker(A)
            self.assertEqual(tracker.observe(HAObservation(B, local)), (False, None))
            state_changed, change = tracker.observe(HAObservation(B, local))
            self.assertTrue(state_changed)
            self.assertEqual((change.previous, change.current), (A, B))
            self.assertEqual(change.should_notify, local == B)
            self.assertEqual(tracker.observe(HAObservation(B, local)), (False, None))

    def test_failback_is_new_event(self):
        tracker = HARoleTracker(A)
        tracker.observe(HAObservation(B, B))
        tracker.observe(HAObservation(B, B))
        self.assertIsNone(tracker.observe(HAObservation(A, A))[1])
        _, change = tracker.observe(HAObservation(A, A))
        self.assertTrue(change.should_notify)
        self.assertEqual((change.previous, change.current), (B, A))

    def test_flapping_and_failed_check_reset_candidate(self):
        tracker = HARoleTracker(A)
        tracker.observe(HAObservation(B, B))
        tracker.observe(HAObservation(A, A))
        self.assertIsNone(tracker.observe(HAObservation(B, B))[1])
        tracker.reset_candidate()  # unavailable exporter / no quorum
        self.assertIsNone(tracker.observe(HAObservation(B, B))[1])
        self.assertTrue(tracker.observe(HAObservation(B, B))[1].should_notify)


class WatchdogHAIntegrationTests(unittest.TestCase):
    def create_shell(self, state_file):
        w = object.__new__(watchdog.Watchdog)
        w.state = watchdog.WatchdogState(state_file)
        w.state.load()
        w.cfg = {"ha_monitor": {"enabled": True}}
        w._ha_cfg = {"enabled": True, "exporter_url": "http://127.0.0.1:9664/metrics"}
        w._ha_enabled = True
        w._ha_tracker = HARoleTracker(w.state.get_ha_node())
        w._ha_error_log_at = 0.0
        w._session = Mock()
        w._boot_time = 123
        w._grace_until = 0.0
        w.host = "CC-HA-test"
        w.runbook_base = "https://example.invalid/runbook"
        w._dispatch = Mock()
        return w

    def test_real_watchdog_event_and_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/state.json"
            w = self.create_shell(path)
            with patch("watchdog.read_ha_observation", side_effect=[
                HAObservation(A, B), HAObservation(B, B),
                HAObservation(B, B), HAObservation(B, B),
            ]):
                for _ in range(4):
                    w._poll_ha()
            w._dispatch.assert_called_once()
            alert = w._dispatch.call_args.args[0]
            self.assertEqual(alert.failure_type, "ha-failover")
            self.assertEqual(alert.severity, "WARNING")
            self.assertEqual((alert.ha_previous_node, alert.ha_new_node), (A, B))
            self.assertNotIn("Container", alert.subject())
            self.assertIn(f"{A} -> {B}", alert.subject())
            saved = json.loads(Path(path).read_text())
            self.assertEqual(saved["version"], 1)
            self.assertEqual(saved["ha_monitor"]["active_node"], B)
            self.assertEqual(saved["containers"], {})

            restarted = self.create_shell(path)
            with patch("watchdog.read_ha_observation",
                       return_value=HAObservation(B, B)):
                restarted._poll_ha()
            restarted._dispatch.assert_not_called()

    def test_failed_exporter_does_not_overwrite_state_or_alert(self):
        with tempfile.TemporaryDirectory() as td:
            w = self.create_shell(f"{td}/state.json")
            w.state.set_ha_node(A)
            w.state.save(w._boot_time)
            w._ha_tracker = HARoleTracker(A)
            with patch("watchdog.read_ha_observation",
                       side_effect=[HAObservation(B, B), TimeoutError("offline"),
                                    HAObservation(B, B), HAObservation(B, B)]):
                for _ in range(4):
                    w._poll_ha()
            self.assertEqual(w.state.get_ha_node(), B)
            w._dispatch.assert_called_once()

    def test_grace_period_delays_detection_and_no_boot_summary_pollution(self):
        with tempfile.TemporaryDirectory() as td:
            w = self.create_shell(f"{td}/state.json")
            w._ha_tracker = HARoleTracker(A)
            w._grace_until = float("inf")
            with patch("watchdog.read_ha_observation") as read:
                w._poll_ha()
                read.assert_not_called()
            w._grace_until = 0
            with patch("watchdog.read_ha_observation",
                       side_effect=[HAObservation(B, B), HAObservation(B, B)]):
                w._poll_ha()
                w._poll_ha()
            w._dispatch.assert_called_once()

    def test_docker_poll_failure_does_not_suppress_ha_poll(self):
        w = object.__new__(watchdog.Watchdog)
        w.cfg = {"check_interval_seconds": 60}
        w._ha_enabled = True
        w._stop_event = Mock()
        w._stop_event.is_set.side_effect = [False, True]
        w._poll_all_containers = Mock(side_effect=RuntimeError("docker outage"))
        w._poll_ha = Mock()
        with patch("watchdog.log.error"):
            w._poll_loop()
        w._poll_ha.assert_called_once()

    def test_disabled_by_default(self):
        self.assertFalse(watchdog.DEFAULT_CONFIG["ha_monitor"]["enabled"])
        w = object.__new__(watchdog.Watchdog)
        w.cfg = {"check_interval_seconds": 60}
        w._ha_enabled = False
        w._stop_event = Mock()
        w._stop_event.is_set.side_effect = [False, True]
        w._poll_all_containers = Mock()
        w._poll_ha = Mock()
        w._poll_loop()
        w._poll_ha.assert_not_called()

    def test_existing_state_schema_and_container_recovery_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/state.json"
            Path(path).write_text(json.dumps({
                "version": 1, "boot_time": 123, "containers": {
                    "old-service": {"alerted_for": "unhealthy", "last_alert_time": 42},
                },
            }))
            state = watchdog.WatchdogState(path)
            containers, boot = state.load()
            self.assertIn("old-service", containers)
            self.assertEqual(boot, 123)
            self.assertEqual(state.get_ha_node(), "")
            state.set_ha_node(A)
            st = state.get("old-service")
            st.alerted_for = "unhealthy"
            st.last_alert_time = 42
            threads = [threading.Thread(target=lambda: [state.save(123) for _ in range(20)])
                       for _ in range(3)]
            for t in threads: t.start()
            for t in threads: t.join()
            saved = json.loads(Path(path).read_text())
            self.assertIn("old-service", saved["containers"])
            self.assertEqual(saved["ha_monitor"]["active_node"], A)


class NotificationTests(unittest.TestCase):
    def make_alert(self):
        return watchdog.AlertPayload(
            "WARNING", "(HA cluster)", "", "CC-HA-test", "ha-failover",
            None, "", "Verify service health.", "https://example.invalid/runbook",
            f"Previous active: {A}; New active: {B}",
            ha_previous_node=A, ha_new_node=B)

    def test_slack_ha_fields_and_no_false_container(self):
        alert = self.make_alert()
        with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "https://example.invalid/slack"}):
            with patch("watchdog.requests.post") as post:
                watchdog.send_slack(alert, {"slack": {"enabled": True}})
        fields = post.call_args.kwargs["json"]["attachments"][0]["fields"]
        keys = [f["title"] for f in fields]
        self.assertIn("Previous active", keys)
        self.assertIn("New active", keys)
        self.assertNotIn("Container", keys)

    def test_syslog_logging_includes_transition_and_dispatches_snmp(self):
        alert = self.make_alert()
        with patch("watchdog.log.warning") as warn, patch("watchdog.send_snmp_trap") as trap:
            watchdog.dispatch_alert(alert, {"alert_channels": ["snmp_trap", "syslog"]})
        self.assertIn(f"{A} -> {B}", warn.call_args.args[1])
        trap.assert_called_once()

    def test_smtp_ha_notification_identifies_role_change(self):
        from email.parser import BytesParser
        from email.policy import default
        alert = self.make_alert()
        cfg = {"smtp": {"enabled": True, "auth": False, "tls": False,
                        "host": "mail.invalid", "port": 25,
                        "sender": "alerts@example.invalid",
                        "recipients": ["ops@example.invalid"]}}
        with patch("smtplib.SMTP") as smtp:
            watchdog.send_smtp(alert, cfg)
        sent = smtp.return_value.__enter__.return_value.sendmail.call_args.args[2]
        parsed = BytesParser(policy=default).parsebytes(sent)
        self.assertIn("HA active node changed", parsed["Subject"])
        body = parsed.get_body(preferencelist=("plain",)).get_content()
        self.assertIn(f"Previous active: {A}", body)
        self.assertIn(f"New active: {B}", body)
        self.assertNotIn("Container:", body)

    def test_container_alert_subject_unchanged(self):
        alert = watchdog.AlertPayload(
            "HIGH", "config_postgres_1", "abc", "host", "unhealthy",
            None, "", "Investigate", "")
        self.assertIn("Container UNHEALTHY", alert.subject())


if __name__ == "__main__":
    unittest.main()
