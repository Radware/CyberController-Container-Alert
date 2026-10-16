"""Validate single customer release package with a fake image; never use Docker."""

import hashlib
import posixpath
import re
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
        self.workspace = tempfile.TemporaryDirectory(prefix="container-alert-package-fake-")
        self.addCleanup(self.workspace.cleanup)
        self.path = Path(self.workspace.name)
        self.fake_image = self.path / "fake-watchdog.tar"
        self.fake_image.write_bytes(b"FAKE BINARY, NOT A DOCKER IMAGE")
        self.dist = self.path / "dist"
        result = subprocess.run(
            ["bash", str(SCRIPT), str(self.fake_image), str(self.dist)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.archive = self.dist / f"container-alert-v{VERSION}.tar.gz"

    def package_files(self):
        with tarfile.open(self.archive, "r:gz") as tf:
            return {
                item.name: tf.extractfile(item).read()
                for item in tf if item.isfile()
            }

    def test_one_complete_release_package(self):
        files = self.package_files()
        required = ("install.sh", "uninstall.sh", "docker-compose.yaml", "VERSION",
                    "README.md", ".env.example",
                    "watchdog-config.yaml.example", "watchdog.tar", "SHA256SUMS",
                    "docs/customer/INSTALLATION_AND_OPERATIONS.md",
                    f"docs/customer/RELEASE_NOTES_V{VERSION}.md")
        for name in required:
            self.assertIn(PREFIX + name, files)
        self.assertEqual(files[PREFIX + "VERSION"].decode().strip(), VERSION)

    def test_bundle_contains_image_not_python_source(self):
        files = self.package_files()
        self.assertEqual(files[PREFIX + "watchdog.tar"], self.fake_image.read_bytes())
        self.assertNotIn(PREFIX + "watchdog.py", files)
        self.assertNotIn(PREFIX + "lifecycle.py", files)
        self.assertNotIn(PREFIX + "Dockerfile", files)
        self.assertNotIn(PREFIX + ".env", files)
        self.assertNotIn(PREFIX + "watchdog-config.yaml", files)
        self.assertFalse(any(path.startswith(PREFIX + "docs/engineering/") for path in files))
        self.assertNotIn(PREFIX + "docs/DEVELOPMENT.md", files)

    def test_every_local_customer_link_is_packaged(self):
        files = self.package_files()
        links = re.compile(r"\\]\\(([^)#]+)?(?:#[a-zA-Z0-9\\-]+)?\\)")
        for name, content in files.items():
            if not name.endswith(".md"):
                continue
            for match in links.finditer(content.decode("utf-8")):
                relative = match.group(1)
                if not relative or relative.startswith(("http:", "https:", "mailto:")):
                    continue
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), relative)
                )
                with self.subTest(markdown=name, target=target):
                    self.assertIn(target, files)

    def test_image_checksum_inside_package(self):
        files = self.package_files()
        expected = hashlib.sha256(self.fake_image.read_bytes()).hexdigest()
        self.assertEqual(files[PREFIX + "SHA256SUMS"].decode().strip(),
                         f"{expected}  watchdog.tar")

    def test_release_checksum_covers_only_package(self):
        self.assertTrue(self.archive.is_file())
        lines = (self.dist / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(lines, [
            f"{hashlib.sha256(self.archive.read_bytes()).hexdigest()}  {self.archive.name}"
        ])
        self.assertEqual(sorted(p.name for p in self.dist.iterdir()),
                         ["SHA256SUMS", self.archive.name])


if __name__ == "__main__":
    unittest.main()
