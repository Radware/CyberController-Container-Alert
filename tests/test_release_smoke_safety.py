"""Fail-closed release smoke preflight tests; never connect to real Docker."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "release-smoke.sh"


class SmokeSafetyTests(unittest.TestCase):
    def try_runner(self, overrides, docker_info=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "docker-invocations.txt"
            stub = root / "docker"
            # Fake Docker deliberately records any invocation, including read-only.
            stub.write_text(
                "#!/bin/sh\n"
                "echo \"$*\" >> \"" + str(marker) + "\"\n"
                "printf '%s\\n' '" + (docker_info or "{}") + "'\n")
            stub.chmod(0o755)
            env = dict(os.environ)
            for key in ("GITHUB_ACTIONS", "SMOKE_RUNNER_KIND", "SMOKE_ENGINE_MARKER",
                        "DOCKER_HOST", "DOCKER_CONTEXT", "SMOKE_ONLY"):
                env.pop(key, None)
            env.update(overrides)
            env["PATH"] = td + os.pathsep + env.get("PATH", "")
            env["SMOKE_OUTPUT_DIR"] = td
            done = subprocess.run(["bash", str(SCRIPT)], env=env, cwd=REPO,
                                  capture_output=True, text=True, timeout=15)
            calls = marker.read_text().splitlines() if marker.exists() else []
            return done.returncode, calls

    def test_default_invocation_fails_without_any_docker_call(self):
        rc, calls = self.try_runner({})
        self.assertNotEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_live_docker_host_rejected_without_any_docker_call(self):
        rc, calls = self.try_runner({
            "GITHUB_ACTIONS": "true", "SMOKE_RUNNER_KIND": "github-hosted",
            "SMOKE_ENGINE_MARKER": "ci-123-1",
            "DOCKER_HOST": "unix:///var/run/docker.sock"})
        self.assertNotEqual(rc, 0)
        self.assertEqual(calls, [])
        rc, calls = self.try_runner({
            "GITHUB_ACTIONS": "true", "SMOKE_RUNNER_KIND": "github-hosted",
            "SMOKE_ENGINE_MARKER": "ci-123-1",
            "DOCKER_HOST": "tcp://192.0.2.41:2375"})
        self.assertNotEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_context_override_rejected(self):
        rc, calls = self.try_runner({
            "GITHUB_ACTIONS": "true", "SMOKE_RUNNER_KIND": "github-hosted",
            "SMOKE_ENGINE_MARKER": "ci-123-1",
            "DOCKER_CONTEXT": "production",
            "DOCKER_HOST": "tcp://127.0.0.1:32768"})
        self.assertNotEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_forged_or_nonempty_engine_fails_before_creating_resources(self):
        base = {"GITHUB_ACTIONS": "true",
                "SMOKE_RUNNER_KIND": "github-hosted",
                "SMOKE_ENGINE_MARKER": "ci-123-1",
                "DOCKER_HOST": "tcp://127.0.0.1:32768"}
        for info in (
            '{"ID":"unexpected","Labels":[],"Containers":0}',
            '{"ID":"test","Labels":["com.radware.container-alert.smoke-engine=ci-123-1"],"Containers":1}',
        ):
            with self.subTest(info=info):
                rc, calls = self.try_runner(base, docker_info=info)
                self.assertNotEqual(rc, 0)
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0].startswith("info --format "), calls)

    def test_self_hosted_runner_rejected(self):
        rc, calls = self.try_runner({
            "GITHUB_ACTIONS": "true", "SMOKE_RUNNER_KIND": "self-hosted",
            "SMOKE_ENGINE_MARKER": "ci-123-1",
            "DOCKER_HOST": "tcp://127.0.0.1:32768"})
        self.assertNotEqual(rc, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
