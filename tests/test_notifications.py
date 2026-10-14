"""Notification consistency and syslog regression tests (no network)."""
import logging
import unittest
from unittest.mock import patch
from email.parser import BytesParser
from email.policy import default

import watchdog


def alert(severity="CRITICAL", failure="crashed"):
    return watchdog.AlertPayload(
        severity, "demo-service", "abcdef123456789", "vision", failure, 137,
        "", "Inspect Docker events and logs.", "https://example.invalid/runbook",
        'Crash probe failed - SIGKILL; OOM not confirmed',
    )


class NotificationTests(unittest.TestCase):
    def test_oom_evidence_only_when_confirmed(self):
        p = alert("CRITICAL", "oom")
        p.probe_detail = ("OOM probe failed - container was OOM-killed by the kernel; "
                          "memory usage=63.0 MB limit=64.0 MB")
        data = watchdog.format_syslog_alert(p)
        self.assertIn('failure_type="oom"', data)
        self.assertIn('memory usage=63.0 MB', data)
        self.assertNotIn('failure_type="crashed"', data)

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
        for fragment in ('event="container_alert"', 'severity="CRITICAL"',
                         'failure_type="crashed"', 'exit_code="137"',
                         'host="vision"', 'container="demo-service"',
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
