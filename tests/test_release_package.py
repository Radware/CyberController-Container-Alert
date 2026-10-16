"""Test release package composition without Docker, image builds, or live hosts."""

import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package_release.sh"
VERSION = (ROOT / "VERSION").read_text().strip()
PREFIX = "CyberController-Container-Alert/"


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="container-alert-release-fake-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.fake_image = self.workspace / "test-watchdog.tar"
        self.fake_image.write_bytes(b"FAKE ARCHIVE - NOT A DOCKER IMAGE\n")
        self.output = self.workspace / "dist"
        result = subprocess.run(
            ["bash", str(SCRIPT), str(self.fake_image), str(self.output)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.online = self.output / f"container-alert-v{VERSION}-online.tar.gz"
        self.offline = self.output / f"container-alert-v{VERSION}-offline.tar.gz"

    @staticmethod
    def members(path):
        with tarfile.open(path, "r:gz") as archive:
            return {member.name: archive.extractfile(member).read()
                    for member in archive if member.isfile()}

    def test_online_bundle_contains_only_installer_files(self):
        contents = self.members(self.online)
        for filename in ("install.sh", "uninstall.sh", "VERSION",
                         "docker-compose.yaml", "README.md", "DEPLOYMENT.md",
                         ".env.example", "watchdog-config.yaml.example",
                         "docs/DEPLOYMENT_ADVANCED.md",
                         "docs/DEVELOPMENT_AND_RELEASE.md",
                         "docs/RELEASE_NOTES_V1.6.0.md"):
            self.assertIn(PREFIX + filename, contents)
        self.assertNotIn(PREFIX + "watchdog.tar", contents)
        self.assertNotIn(PREFIX + "watchdog.py", contents)
        self.assertNotIn(PREFIX + "Dockerfile", contents)

    def test_offline_bundle_adds_image_and_internal_checksum(self):
        contents = self.members(self.offline)
        self.assertEqual(contents[PREFIX + "watchdog.tar"],
                         self.fake_image.read_bytes())
        expected = hashlib.sha256(self.fake_image.read_bytes()).hexdigest()
        self.assertIn(expected, contents[PREFIX + "SHA256SUMS"].decode())
        self.assertIn("watchdog.tar", contents[PREFIX + "SHA256SUMS"].decode())
        self.assertNotIn(PREFIX + "watchdog.py", contents)

    def test_both_bundles_share_identical_installer_and_version(self):
        online = self.members(self.online)
        offline = self.members(self.offline)
        for path, content in online.items():
            self.assertEqual(offline[path], content, path)
        self.assertEqual(online[PREFIX + "VERSION"].decode().strip(), VERSION)

    def test_release_checksum_manifest_covers_every_asset(self):
        manifest = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(manifest), 3)
        names = set()
        for line in manifest:
            digest, name = line.split(maxsplit=1)
            names.add(name)
            path = self.output / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual(names, {"watchdog.tar", self.online.name, self.offline.name})


if __name__ == "__main__":
    unittest.main()
