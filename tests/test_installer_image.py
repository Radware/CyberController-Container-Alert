"""Validate the single bundled installer using fake Docker only."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()


class InstallerImageTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="watchdog-installer-fake-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        self.state = self.root / "fake-state"
        self.state.mkdir()
        shutil.copy2(ROOT / "install.sh", self.root / "install.sh")
        (self.root / "VERSION").write_text(VERSION + "\n")
        self.log = self.root / "fake-docker-calls.log"
        self.log.write_text("")
        self.archive = b"fake image bytes, not a real Docker image"
        self.env = os.environ.copy()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            FAKE_DOCKER_LOG=str(self.log),
            FAKE_STATE_DIR=str(self.state),
            FAKE_VERSION=VERSION,
            FAKE_IMAGE_PRESENT="0",
        )
        docker = self.bin / "docker"
        docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
    image)
        if [ "$2" = inspect ]; then
            if [ "$FAKE_IMAGE_PRESENT" = 1 ] || [ -f "$FAKE_STATE_DIR/loaded" ]; then
                case "$*" in
                    *org.opencontainers.image.version*) echo "$FAKE_VERSION" ;;
                    *'{{.Id}}'*) echo 'sha256:fake-image-id' ;;
                esac
                exit 0
            fi
            exit 1
        fi ;;
    load)
        touch "$FAKE_STATE_DIR/loaded"
        echo "Loaded image: watchdog:$FAKE_TAG_VERSION"
        exit 0 ;;
    config|up) exit 0 ;;
esac
echo "Unexpected fake Docker arguments: $*" >&2
exit 97
""")
        docker.chmod(0o755)
        self.env["FAKE_TAG_VERSION"] = VERSION

    def write_archive(self, with_checksum=True, valid_checksum=True):
        (self.root / "watchdog.tar").write_bytes(self.archive)
        if with_checksum:
            digest = hashlib.sha256(self.archive).hexdigest()
            if not valid_checksum:
                digest = "0" * 64
            (self.root / "SHA256SUMS").write_text(
                f"{digest}  watchdog.tar\n"
            )

    def run_function(self, body="load_docker_image"):
        result = subprocess.run(
            ["bash", "-c", "source ./install.sh; " + body],
            cwd=self.root, env=self.env,
            text=True, capture_output=True, timeout=10,
        )
        result.output = result.stdout + result.stderr
        return result

    def calls(self):
        return self.log.read_text().splitlines()

    def test_valid_package_loads_image(self):
        self.write_archive()
        out = self.run_function()
        self.assertEqual(out.returncode, 0, out.output)
        self.assertTrue(any(c.startswith("load ") for c in self.calls()))

    def test_existing_local_image_is_not_silently_trusted(self):
        self.write_archive()
        self.env["FAKE_IMAGE_PRESENT"] = "1"
        out = self.run_function()
        self.assertEqual(out.returncode, 0, out.output)
        self.assertTrue(any(c.startswith("load ") for c in self.calls()))

    def test_missing_package_fails_without_docker_mutations(self):
        out = self.run_function()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("Incomplete release package", out.output)
        self.assertEqual(self.calls(), [])

    def test_missing_checksum_rejected_before_loading(self):
        self.write_archive(with_checksum=False)
        out = self.run_function()
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_invalid_checksum_rejected_before_loading(self):
        self.write_archive(valid_checksum=False)
        out = self.run_function()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("checksum mismatch", out.output)
        self.assertEqual(self.calls(), [])

    def test_wrong_image_version_label_rejected(self):
        self.write_archive()
        self.env["FAKE_VERSION"] = "unexpected-version"
        out = self.run_function()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("does not match", out.output)

    def test_wrong_image_tag_rejected(self):
        self.write_archive()
        self.env["FAKE_TAG_VERSION"] = "old"
        out = self.run_function()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("expected tag", out.output)

    def test_existing_configuration_is_not_regenerated(self):
        (self.root / ".env").write_text("SECRET=keep\n")
        out = self.run_function("configure_all")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual((self.root / ".env").read_text(), "SECRET=keep\n")
        self.assertFalse((self.root / "watchdog-config.yaml").exists())
        self.assertEqual(self.calls(), [])

    def test_recreate_targets_watchdog_only(self):
        out = self.run_function("DC=docker; start_watchdog")
        self.assertEqual(out.returncode, 0, out.output)
        self.assertIn("config -q", self.calls())
        self.assertIn("up -d --no-deps --force-recreate docker-container-watchdog",
                      self.calls())
        self.assertFalse(any("down" in c for c in self.calls()))

    def test_no_network_image_download_path(self):
        source = (ROOT / "install.sh").read_text()
        self.assertNotIn("download_release_image()", source)
        self.assertNotIn("curl --fail", source)
        self.assertNotIn('docker build', source)


if __name__ == "__main__":
    unittest.main()
