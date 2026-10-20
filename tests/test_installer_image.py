"""Validate the single bundled installer using fake Docker only."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()


def config_key_paths(config: dict, prefix: str = "") -> set[str]:
    """All active YAML key paths, including parent objects and empty mappings."""
    found: set[str] = set()
    for key, value in config.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        found.add(path)
        if isinstance(value, dict):
            found.update(config_key_paths(value, path))
    return found


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

    def run_function(self, body="load_docker_image", input_data=None):
        result = subprocess.run(
            ["bash", "-c", "source ./install.sh; " + body],
            cwd=self.root, env=self.env, input=input_data,
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

    def test_keep_existing_configuration_does_not_add_ha(self):
        # Upgrading with the default "keep" answer must preserve active YAML.
        existing = "\n".join([
            "syslog:", "  enabled: true",
            "ignored_container_events:",
            "  - label:",
            "      com.radware.cybercontroller.role: mariadb-ha-syntax-check",
            "    events: [die]", "",
        ])
        (self.root / "watchdog-config.yaml").write_text(existing)
        (self.root / ".env").write_text(
            "\n".join(["DOCKER_GID=999", "WATCHDOG_VERSION=1.5.4", ""])
        )
        body = ('detect_docker_gid() { echo 999; }; '
                '_publish_file() { cp "$1" "$2"; }; '
                'harden_config_permissions() { :; }; configure_all')
        result = self.run_function(body, input_data="\n")
        self.assertEqual(result.returncode, 0, result.output)
        self.assertEqual((self.root / "watchdog-config.yaml").read_text(), existing)
        self.assertIn("WATCHDOG_VERSION=" + VERSION,
                      (self.root / ".env").read_text())
        self.assertEqual(self.calls(), [])

    def test_generated_config_includes_optional_ha_disabled(self):
        # Isolated wizard with fake file ownership / socket GID: no Docker calls.
        body = ('detect_docker_gid() { echo 999; }; '
                '_publish_file() { cp "$1" "$2"; }; '
                'DC=docker; configure_all')
        result = self.run_function(
            body, input_data="\n".join(["4", "", "", "", "", "", ""]) + "\n"
        )
        self.assertEqual(result.returncode, 0, result.output)
        config = yaml.safe_load((self.root / "watchdog-config.yaml").read_text())
        self.assertEqual(config["ha_monitor"], {
            "enabled": False, "exporter_url": "http://127.0.0.1:9664/metrics",
        })
        self.assertEqual(config["ignored_container_events"], [])
        self.assertEqual(self.calls(), [])

    def test_generated_yaml_matches_all_active_reference_keys_and_defaults(self):
        # Regression guard: every new reference YAML field must also be
        # generated by the wizard. Runs only in the fake-Docker workspace.
        body = ('detect_docker_gid() { echo 999; }; '
                '_publish_file() { cp "$1" "$2"; }; '
                'DC=docker; configure_all')
        result = self.run_function(
            body, input_data="\n".join(["4", "", "", "", "", "", ""]) + "\n"
        )
        self.assertEqual(result.returncode, 0, result.output)
        generated = yaml.safe_load((self.root / "watchdog-config.yaml").read_text())
        template = yaml.safe_load((ROOT / "watchdog-config.yaml.example").read_text())

        # Key parity in BOTH directions, including nested settings, avoids
        # silently omitting a new option (or documenting an obsolete option).
        self.assertEqual(config_key_paths(generated), config_key_paths(template))
        # Wizard-picked destinations/channels necessarily differ from
        # example placeholders; all non-interactive defaults must agree.
        non_interactive = (
            "check_interval_seconds", "cooldown_minutes",
            "restart_threshold", "restart_window_minutes",
            "restart_recovery_stability_seconds", "unhealthy_cycles_threshold",
            "alert_on_recovery", "lifecycle_detection",
            "excluded_containers", "ignored_container_events",
            "log_level", "log_file", "state_file", "boot_grace_seconds",
            "runbook_base_url", "auto_health_check", "container_health_checks",
            "ha_monitor",
        )
        for setting in non_interactive:
            with self.subTest(setting=setting):
                self.assertEqual(generated[setting], template[setting])

        self.assertEqual(generated["lifecycle_detection"],
                         {"enabled": True, "correlation_window_seconds": 3})
        self.assertEqual(generated["restart_recovery_stability_seconds"], 120)
        self.assertIs(generated["alert_on_recovery"], True)
        self.assertEqual(generated["ignored_container_events"], [])
        self.assertEqual(self.calls(), [])

    def test_snmpv3_wizard_retains_reference_settings_and_keeps_secrets_in_env(self):
        # Conditional SNMPv3 settings are additional to the active reference
        # schema, not a reason to omit any common setting.
        body = ('detect_docker_gid() { echo 999; }; '
                '_publish_file() { cp "$1" "$2"; }; '
                'DC=docker; configure_all')
        inputs = [
            "3", "nms.example.invalid", "162", "v3",
            "snmpuser", "SHA", "example-auth-value", "AES",
            "example-priv-value", "", "",
        ]
        result = self.run_function(body, input_data=chr(10).join(inputs) + chr(10))
        self.assertEqual(result.returncode, 0, result.output)
        config = yaml.safe_load((self.root / "watchdog-config.yaml").read_text())
        template = yaml.safe_load((ROOT / "watchdog-config.yaml.example").read_text())
        self.assertTrue(config_key_paths(template) <= config_key_paths(config))
        self.assertEqual(config["alert_channels"], ["snmp_trap"])
        self.assertEqual(config["snmp_trap"]["version"], "v3")
        self.assertEqual(config["snmp_trap"]["v3_username"], "snmpuser")
        self.assertEqual(config["snmp_trap"]["v3_auth_key_env"], "SNMP_V3_AUTH_KEY")
        self.assertEqual(config["snmp_trap"]["v3_priv_key_env"], "SNMP_V3_PRIV_KEY")
        self.assertEqual(config["lifecycle_detection"], template["lifecycle_detection"])
        self.assertEqual(config["ignored_container_events"], [])
        self.assertNotIn("example-auth-value",
                         (self.root / "watchdog-config.yaml").read_text())
        self.assertNotIn("example-priv-value",
                         (self.root / "watchdog-config.yaml").read_text())
        env = (self.root / ".env").read_text()
        self.assertIn("SNMP_V3_AUTH_KEY=example-auth-value", env)
        self.assertIn("SNMP_V3_PRIV_KEY=example-priv-value", env)
        self.assertEqual(self.calls(), [])

    def test_generated_config_can_enable_ha_with_custom_exporter(self):
        body = ('detect_docker_gid() { echo 999; }; '
                '_publish_file() { cp "$1" "$2"; }; '
                'DC=docker; configure_all')
        result = self.run_function(
            body, input_data="\n".join(
                ["4", "", "", "", "", "yes",
                 "http://127.0.0.1:9777/metrics", ""]
            ) + "\n"
        )
        self.assertEqual(result.returncode, 0, result.output)
        config = yaml.safe_load((self.root / "watchdog-config.yaml").read_text())
        self.assertEqual(config["ha_monitor"], {
            "enabled": True, "exporter_url": "http://127.0.0.1:9777/metrics",
        })
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
