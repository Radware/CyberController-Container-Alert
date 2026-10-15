"""Installer decisions use fake Docker and fake downloads; never a real daemon."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "install.sh"
VERSION = "1.6.0"


class InstallerImageTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="watchdog-installer-fake-")
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        self.release = self.root / "fake-release"
        self.release.mkdir()
        self.state = self.root / "fake-state"
        self.state.mkdir()
        shutil.copy2(SOURCE, self.root / "install.sh")
        (self.root / "VERSION").write_text(VERSION + "\n")
        self.log = self.root / "fake-docker-calls.log"
        self.log.write_text("")
        self.archive = b"small fake release archive, never a real Docker image"
        self.write_release_archive(self.release)

        docker = self.bin / "docker"
        docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
    image)
        if [ "$2" = inspect ]; then
            if [ "$FAKE_IMAGE_PRESENT" = 1 ] || [ -f "$FAKE_STATE_DIR/loaded" ]; then
                case "$*" in
                    *org.opencontainers.image.version*) printf '%s\\n' "$FAKE_VERSION" ;;
                    *'{{.Id}}'*) echo 'sha256:fake-image-id' ;;
                esac
                exit 0
            fi
            exit 1
        fi ;;
    load)
        touch "$FAKE_STATE_DIR/loaded"
        echo 'Loaded image: watchdog:1.6.0'
        exit 0 ;;
    config|up)
        exit 0 ;;
esac
echo "Unexpected fake Docker arguments: $*" >&2
exit 97
""")
        docker.chmod(0o755)
        curl = self.bin / "curl"
        curl.write_text("""#!/usr/bin/env bash
[ "$FAKE_CURL_FAIL" != 1 ] || exit 22
out=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) out="$2"; shift 2 ;;
        *) url="$1"; shift ;;
    esac
done
case "$url" in
    */watchdog.tar) cp "$FAKE_RELEASE_DIR/watchdog.tar" "$out" ;;
    */SHA256SUMS) cp "$FAKE_RELEASE_DIR/SHA256SUMS" "$out" ;;
    *) echo "Unexpected download URL" >&2; exit 22 ;;
esac
""")
        curl.chmod(0o755)

        self.env = os.environ.copy()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            FAKE_DOCKER_LOG=str(self.log),
            FAKE_STATE_DIR=str(self.state),
            FAKE_RELEASE_DIR=str(self.release),
            FAKE_VERSION=VERSION,
            FAKE_IMAGE_PRESENT="0",
            FAKE_CURL_FAIL="0",
        )

    def write_release_archive(self, folder, valid=True):
        (folder / "watchdog.tar").write_bytes(self.archive)
        digest = hashlib.sha256(self.archive).hexdigest()
        if not valid:
            digest = "0" * 64
        (folder / "SHA256SUMS").write_text(f"{digest}  watchdog.tar\n")

    def run_installer(self, task="load_docker_image", online=False):
        # Source install.sh. Overriding prompts never invokes main or Docker.
        prompt = (
            '_prompt_yn() { case "$1" in '
            '*"Can this host download"*) printf "'
            + ("true" if online else "false")
            + '";; *) printf "true";; esac; }; '
        )
        command = "source ./install.sh; " + prompt + task
        return subprocess.run(
            ["bash", "-c", command],
            env=self.env,
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )

    def docker_calls(self):
        return self.log.read_text().splitlines()

    def test_offline_uses_matching_local_image_without_loading(self):
        self.env["FAKE_IMAGE_PRESENT"] = "1"
        res = self.run_installer()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertFalse(any(s.startswith("load ") for s in self.docker_calls()))

    def test_offline_loads_checksum_verified_archive(self):
        shutil.copy2(self.release / "watchdog.tar", self.root / "watchdog.tar")
        shutil.copy2(self.release / "SHA256SUMS", self.root / "SHA256SUMS")
        res = self.run_installer()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertTrue(any(s.startswith("load ") for s in self.docker_calls()))

    def test_bad_offline_checksum_is_rejected_before_docker_load(self):
        self.write_release_archive(self.root, valid=False)
        res = self.run_installer()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("checksum mismatch", res.stdout)
        self.assertFalse(any(s.startswith("load ") for s in self.docker_calls()))

    def test_unverified_local_image_is_rejected(self):
        self.env["FAKE_IMAGE_PRESENT"] = "1"
        self.env["FAKE_VERSION"] = "old-version"
        res = self.run_installer()
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(any(s.startswith("load ") for s in self.docker_calls()))

    def test_online_download_and_load_never_builds(self):
        res = self.run_installer(online=True)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertTrue(any(s.startswith("load ") for s in self.docker_calls()))
        self.assertFalse(any("build" in s for s in self.docker_calls()))

    def test_online_failure_can_use_matching_local_image(self):
        self.env["FAKE_CURL_FAIL"] = "1"
        self.env["FAKE_IMAGE_PRESENT"] = "1"
        res = self.run_installer(online=True)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertFalse(any(s.startswith("load ") for s in self.docker_calls()))

    def test_missing_image_fails_without_docker_mutations(self):
        res = self.run_installer()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("No running containers were changed", res.stdout)
        self.assertFalse(any(s.startswith(("load ", "up ", "run ", "stop ", "build ")) for s in self.docker_calls()))

    def test_incomplete_existing_configuration_does_not_reset(self):
        (self.root / ".env").write_text("SENSITIVE=keep\n")
        res = self.run_installer(task="configure_all")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual((self.root / ".env").read_text(), "SENSITIVE=keep\n")
        self.assertFalse((self.root / "watchdog-config.yaml").exists())
        self.assertEqual(self.docker_calls(), [])

    def test_start_recreates_watchdog_only(self):
        # The service call below is a fake Docker executable, not a host daemon.
        res = self.run_installer(task='DC=docker; start_watchdog')
        self.assertEqual(res.returncode, 0, res.stdout)
        calls = self.docker_calls()
        self.assertIn("config -q", calls)
        self.assertIn("up -d --no-deps --force-recreate docker-container-watchdog", calls)
        self.assertFalse(any("down" in s for s in calls))


if __name__ == "__main__":
    unittest.main()
