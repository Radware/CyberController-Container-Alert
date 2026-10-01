import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import watchdog


class FakeExecContainer:
    name = "svc"
    id = "abc123"

    def __init__(self, delay=0.0, exit_code=0):
        self.delay = delay
        self.exit_code = exit_code

    def exec_run(self, command):
        time.sleep(self.delay)
        return SimpleNamespace(exit_code=self.exit_code, output=b"probe output")


class WatchdogRegressionTests(unittest.TestCase):
    def make_watchdog_shell(self):
        w = object.__new__(watchdog.Watchdog)
        w._manual_exec_lock = threading.Lock()
        w._manual_exec_inflight = set()
        return w
    def test_manual_exec_timeout_does_not_block_poll_thread(self):
        w = self.make_watchdog_shell()
        c = FakeExecContainer(delay=0.15)
        started = time.monotonic()
        healthy, detail = w._manual_exec_health_check(c, ["check"], 0.02)
        elapsed = time.monotonic() - started
        self.assertFalse(healthy)
        self.assertIn("timed out", detail)
        self.assertLess(elapsed, 0.10)

        healthy, detail = w._manual_exec_health_check(c, ["check"], 0.02)
        self.assertFalse(healthy)
        self.assertIn("previous probe", detail)
        time.sleep(0.18)

    def test_invalid_manual_timeout_falls_back_without_raising(self):
        w = self.make_watchdog_shell()
        c = FakeExecContainer()
        healthy, detail = w._manual_health_check(
            c, {"type": "exec", "command": ["true"], "timeout_seconds": "bad"}
        )
        self.assertTrue(healthy)
        self.assertEqual(detail, "")

    def test_recovery_disabled_still_clears_open_state(self):
        w = object.__new__(watchdog.Watchdog)
        w.cfg = {"alert_on_recovery": False}
        w.state = watchdog.WatchdogState("")
        w._boot_time = 123
        state = w.state.get("svc")
        state.alerted_for = "crashed"
        w._maybe_alert_recovery(SimpleNamespace(name="svc"), state)
        self.assertEqual(state.alerted_for, "")
    def test_reboot_summary_uses_current_state_not_folded_failures(self):
        w = object.__new__(watchdog.Watchdog)
        w.cfg = {}
        w.host = "host"
        w.runbook_base = "runbook"
        w._boot_time = 1
        w._grace_until = time.time() - 1
        w._folded_lock = threading.Lock()
        w._folded = [
            watchdog.AlertPayload("HIGH", "fixed", "", "host", "crashed",
                                  None, "", "", ""),
            watchdog.AlertPayload("INFO", "fixed", "", "host", "recovered",
                                  None, "", "", ""),
        ]
        w.state = watchdog.WatchdogState("")
        still = w.state.get("still")
        still.alerted_for = "unhealthy"
        w._container_tally = lambda: (2, 1, ["down"])

        with patch("watchdog.dispatch_alert") as dispatch:
            w._flush_boot_summary()

        payload = dispatch.call_args.args[0]
        self.assertEqual(payload.severity, "HIGH")
        self.assertIn("Recovered since last alert: fixed", payload.probe_detail)
        self.assertIn("Still failing: down, still", payload.probe_detail)
        self.assertNotIn("Still failing: down, fixed", payload.probe_detail)

    def test_state_save_produces_valid_json_under_concurrency(self):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/state.json"
            state = watchdog.WatchdogState(path)
            st = state.get("svc")
            st.alerted_for = "crashed"
            st.last_alert_time = 42.0
            threads = [
                threading.Thread(target=lambda: [state.save(123) for _ in range(20)])
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            with open(path) as f:
                record = json.load(f)
            self.assertEqual(record["containers"]["svc"]["alerted_for"], "crashed")


if __name__ == "__main__":
    unittest.main()
