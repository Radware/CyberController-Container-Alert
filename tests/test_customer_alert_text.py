"""Customer alert wording and channel consistency for v1.6.0 (no Docker/network)."""
import unittest
from unittest.mock import patch
from email.parser import BytesParser
from email.policy import default

import watchdog


class CustomerAlertTextTests(unittest.TestCase):
    EXPECTED = {
        "crashed": "Docker reported an unexpected container termination. Run: docker logs <name> --tail 50. The exit code and detection details provide additional context.",
        "oom": "Docker reported that the container was terminated due to an out-of-memory condition.",
        "unhealthy": "The container is running, but a monitoring health check is failing.",
        "restart-loop": "The container has restarted repeatedly within the configured monitoring window. This pattern may be associated with application errors, resource pressure, or unavailable dependencies. The underlying cause has not been determined.",
        "recovered": "The previously reported alert condition has cleared according to the available monitoring checks.",
        "reboot-summary": "Monitoring has resumed following a host reboot. The reported summary includes container status, recovered incidents, and any conditions that remain unresolved.",
    }

    def test_all_approved_messages_exact_and_informational(self):
        self.assertEqual(set(watchdog.ACTIONS), set(self.EXPECTED))
        for reason, message in self.EXPECTED.items():
            with self.subTest(reason=reason):
                self.assertEqual(watchdog.ACTIONS[reason][1], message)
                self.assertNotIn("docker-compose.yaml", message)
                self.assertNotIn("Increase memory limit", message)
                self.assertNotIn("fix application crash", message)
        self.assertIn("Run: docker logs <name> --tail 50", self.EXPECTED["crashed"])

    def test_fallback_is_informative_and_preserves_warning(self):
        container = type("Container", (), {
            "name": "dummy", "id": "abc123abc123",
            "logs": lambda self, tail, timestamps: b"",
        })()
        payload = watchdog.build_payload(container, "new-unknown-type",
                                         "test-host", "https://example.invalid")
        self.assertEqual(payload.severity, "WARNING")
        self.assertEqual(
            payload.recommended_action,
            "A container monitoring event was reported. The failure type and available detection details provide additional context.")

    def test_crash_replaces_name_and_syslog_preserves_action_key(self):
        container = type("Container", (), {
            "name": "dummy", "id": "abc123abc123",
            "logs": lambda self, tail, timestamps: b"",
        })()
        payload = watchdog.build_payload(
            container, "crashed", "test-host", "https://example.invalid",
            exit_code=2, probe_detail="Crash probe failed - exit code 2")
        self.assertIn("docker logs dummy --tail 50", payload.recommended_action)
        record = watchdog.format_syslog_alert(payload)
        self.assertIn('action="Docker reported an unexpected container termination.', record)
        self.assertIn('failure_type="crashed"', record)
        self.assertIn('exit_code="2"', record)
        self.assertIn('detection="Crash probe failed - exit code 2"', record)

    def test_email_and_slack_use_context_title(self):
        payload = watchdog.AlertPayload(
            "CRITICAL", "dummy", "id", "host", "oom", 137, "",
            self.EXPECTED["oom"], "", "OOM probe failed")
        cfg = {"smtp": {"enabled": True, "auth": False, "tls": False,
                        "host": "example.invalid", "port": 25,
                        "sender": "test@example.invalid",
                        "recipients": ["recipient@example.invalid"]}}
        with patch("watchdog.smtplib.SMTP") as smtp:
            watchdog.send_smtp(payload, cfg)
        raw = smtp.return_value.__enter__.return_value.sendmail.call_args.args[2]
        body = BytesParser(policy=default).parsebytes(raw).get_body(
            preferencelist=("plain",)).get_content()
        self.assertIn("Context: " + self.EXPECTED["oom"], body)
        self.assertNotIn("Action:", body)
        with patch.dict("watchdog.os.environ", {"SLACK_WEBHOOK_URL": "https://example.invalid"}):
            with patch("watchdog.requests.post") as post:
                watchdog.send_slack(payload, {"slack": {"enabled": True}})
        fields = post.call_args.kwargs["json"]["attachments"][0]["fields"]
        mapping = {field["title"]: field["value"] for field in fields}
        self.assertEqual(mapping["Context"], self.EXPECTED["oom"])
        self.assertNotIn("Action", mapping)

    def test_ha_guidance_remains_unchanged(self):
        from inspect import getsource
        self.assertIn(
            "Verify whether the HA switchover was expected and check service health.",
            getsource(watchdog.Watchdog))


if __name__ == "__main__":
    unittest.main()
