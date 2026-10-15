"""Notification consistency and syslog regression tests (no network)."""
import logging
import socket
import tempfile
import unittest
from unittest.mock import patch
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import watchdog


def alert(severity="CRITICAL", failure="crashed"):
    return watchdog.AlertPayload(
        severity, "demo-service", "abcdef123456789", "test-host", failure, 137,
        "", "Inspect Docker events and logs.", "https://example.invalid/runbook",
        'Crash probe failed - SIGKILL; OOM not confirmed',
    )


class NotificationTests(unittest.TestCase):
    def test_oom_evidence_only_when_confirmed(self):
        p = alert("CRITICAL", "oom")
        p.probe_detail = ("OOM probe failed - container was OOM-killed by the kernel; "
                          "post-event memory snapshot: usage=63.0 MB, limit=64.0 MB")
        data = watchdog.format_syslog_alert(p)
        self.assertIn('failure_type="oom"', data)
        self.assertIn('post-event memory snapshot: usage=63.0 MB', data)
        self.assertNotIn('failure_type="crashed"', data)

    def test_real_syslog_keeps_recovery_and_ha_at_local_warning_or_error(self):
        for level in ("WARNING", "ERROR"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as td:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
                    receiver.bind(("127.0.0.1", 0))
                    receiver.settimeout(0.6)
                    watchdog.setup_logging(
                        level, f"{td}/watchdog.log",
                        {"enabled": True, "host": "127.0.0.1",
                         "port": receiver.getsockname()[1], "protocol": "udp"}
                    )
                    try:
                        watchdog.log.info("routine polling must stay local")
                        recovery = alert("INFO", "recovered")
                        recovery.exit_code = None
                        watchdog.dispatch_alert(recovery, {"alert_channels": []})
                        ha = watchdog.AlertPayload(
                            "WARNING", "(HA cluster)", "", "test-host", "ha-failover",
                            None, "", "Check cluster health", "",
                            "HA Operator promoted to new node",
                            ha_previous_node="node-a", ha_new_node="node-b"
                        )
                        watchdog.dispatch_alert(ha, {"alert_channels": []})
                        watchdog.log.error("HA check unavailable (TimeoutError): offline")
                        received = [receiver.recvfrom(8192)[0].decode()
                                    for _ in range(3)]
                        with self.assertRaises(socket.timeout):
                            receiver.recvfrom(8192)
                        self.assertEqual(
                            [int(msg.split(">", 1)[0][1:]) % 8 for msg in received],
                            [6, 4, 3])
                        self.assertIn('failure_type="recovered"', received[0])
                        self.assertIn('failure_type="ha-failover"', received[1])
                        self.assertIn("HA check unavailable", received[2])
                        self.assertFalse(any("routine polling" in msg for msg in received))
                        text = Path(f"{td}/watchdog.log").read_text()
                        self.assertNotIn("routine polling", text)
                        self.assertNotIn('failure_type="recovered"', text)
                        if level == "ERROR":
                            self.assertNotIn('failure_type="ha-failover"', text)
                        else:
                            self.assertIn('failure_type="ha-failover"', text)
                    finally:
                        logging.basicConfig(
                            handlers=[logging.NullHandler()],
                            level=logging.WARNING, force=True)

    def test_pseudo_container_fields_absent_in_ha_and_reboot_events(self):
        for failure, container_name in (
            ("ha-failover", "(HA cluster)"),
            ("reboot-summary", "(post-reboot summary)")
        ):
            with self.subTest(failure=failure):
                p = watchdog.AlertPayload(
                    "INFO", container_name, "", "test-host", failure, None, "",
                    "Check cluster", "", "Event details",
                    ha_previous_node="node-a", ha_new_node="node-b")
                record = watchdog.format_syslog_alert(p)
                self.assertIn('event="watchdog_alert"', record)
                self.assertIn(f'failure_type="{failure}"', record)
                self.assertNotIn('container=', record)
                self.assertNotIn('container_id=', record)
                self.assertIn('host="test-host"', record)
                if failure == "ha-failover":
                    self.assertIn('previous_active="node-a"', record)
                    self.assertIn('new_active="node-b"', record)

    def test_memory_snapshot_omits_missing_zero_and_unavailable_stats(self):
        from unittest.mock import Mock
        container = Mock()
        container.stats.return_value = {
            "memory_stats": {"usage": 0, "limit": 67108864, "max_usage": 12582912}
        }
        sample = watchdog.get_memory_stats(container)
        self.assertIn("post-event memory snapshot:", sample)
        self.assertIn("limit=64.0 MB", sample)
        self.assertIn("peak=12.0 MB", sample)
        self.assertNotIn("usage=", sample)
        container.stats.return_value = {"memory_stats": {}}
        self.assertEqual(watchdog.get_memory_stats(container), "")
        container.stats.side_effect = RuntimeError("unavailable after OOM")
        self.assertEqual(watchdog.get_memory_stats(container), "")

    def test_native_levels_match_incident_severity(self):
        for severity, expected in (("CRITICAL", logging.CRITICAL),
                                   ("HIGH", logging.ERROR),
                                   ("WARNING", logging.WARNING),
                                   ("INFO", logging.INFO)):
            with self.subTest(severity=severity):
                p = alert(severity)
                with patch("watchdog.log.log") as log:
                    watchdog.dispatch_alert(p, {"alert_channels": []})
                self.assertEqual(log.call_args.args[0], expected)
                self.assertIn(f'severity="{severity}"', log.call_args.args[1])
                self.assertTrue(log.call_args.kwargs["extra"]["watchdog_alert"])

    def test_syslog_filter_rejects_poll_noise_and_keeps_recovery_and_errors(self):
        f = watchdog.AlertSyslogFilter()
        def record(level, is_alert=False):
            r = logging.LogRecord("watchdog", level, __file__, 1, "message", (), None)
            if is_alert:
                r.watchdog_alert = True
            return r
        self.assertFalse(f.filter(record(logging.INFO)))
        self.assertFalse(f.filter(record(logging.WARNING)))
        self.assertTrue(f.filter(record(logging.INFO, True)))
        self.assertTrue(f.filter(record(logging.WARNING, True)))
        self.assertTrue(f.filter(record(logging.ERROR)))

    def test_syslog_has_evidence_without_false_oom_and_escapes_content(self):
        p = alert()
        p.probe_detail = 'line one\nline "two"'
        data = watchdog.format_syslog_alert(p)
        for fragment in ('event="watchdog_alert"', 'severity="CRITICAL"',
                         'failure_type="crashed"', 'exit_code="137"',
                         'host="test-host"', 'container="demo-service"',
                         'container_id="abcdef123456"',
                         'probe_type="Docker event (crash)"',
                         'action="Inspect Docker events and logs."'):
            self.assertIn(fragment, data)
        self.assertIn(r'line one\nline \"two\"', data)
        self.assertNotIn('failure_type="oom"', data)
        self.assertNotIn("\n", data)

    def test_long_untrusted_detection_is_bounded(self):
        p = alert()
        p.probe_detail = "x" * 20000
        data = watchdog.format_syslog_alert(p)
        self.assertLess(len(data), 2000)
        self.assertIn('...', data)

    def test_email_includes_detection_without_assuming_oom(self):
        p = alert()
        cfg = {"smtp": {"enabled": True, "auth": False, "tls": False,
                "host": "mail.invalid", "port": 25,
                "sender": "alerts@example.invalid",
                "recipients": ["ops@example.invalid"]}}
        with patch("smtplib.SMTP") as smtp:
            watchdog.send_smtp(p, cfg)
        sent = smtp.return_value.__enter__.return_value.sendmail.call_args.args[2]
        body = BytesParser(policy=default).parsebytes(sent).get_body(
            preferencelist=("plain",)).get_content()
        for item in ("Container ID: abcdef123456", "Severity: CRITICAL",
                     "Exit Code: 137", "Probe Type: Docker event (crash)",
                     "OOM not confirmed", "Runbook: https://example.invalid/runbook"):
            self.assertIn(item, body)

    def test_slack_fields_match_alert_payload(self):
        p = alert()
        with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "https://example.invalid/slack"}):
            with patch("watchdog.requests.post") as post:
                watchdog.send_slack(p, {"slack": {"enabled": True}})
        fields = post.call_args.kwargs["json"]["attachments"][0]["fields"]
        values = {field["title"]: field["value"] for field in fields}
        self.assertEqual(values["Severity"], "CRITICAL")
        self.assertEqual(values["Exit Code"], "137")
        self.assertEqual(values["Detection"], p.probe_detail)


if __name__ == "__main__":
    unittest.main()
