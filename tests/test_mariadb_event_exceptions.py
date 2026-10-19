"""Non-destructive tests for built-in MariaDB helper and optional label policies."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import yaml
import watchdog


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "reg.radware.com:18442/kvision/kvision_infra_mariadb:1.30.0"
MOUNT = "/mnt/cli/tmp/mariadb_dump.sql.gz"


def helper(name="zen_hypatia", *, image=IMAGE, auto_remove=True, mount=MOUNT, labels=None):
    labels = {} if labels is None else labels
    return SimpleNamespace(
        name=name,
        labels=labels,
        image=SimpleNamespace(tags=[]),
        attrs={
            "Config": {"Image": image, "Labels": labels},
            "HostConfig": {"AutoRemove": auto_remove},
            "Mounts": [{"Source": mount, "Destination": mount}],
        },
    )


class MariaDBEventExceptionsTests(unittest.TestCase):
    def test_unlabelled_realistic_helper_uses_builtin_predicate(self):
        container = helper()
        attrs = {"name": "zen_hypatia", "image": IMAGE, "exitCode": "137"}
        self.assertEqual(watchdog.DEFAULT_CONFIG["ignored_container_events"], [])
        self.assertFalse(watchdog.is_ignored_container_event(
            container, attrs, "die", watchdog.DEFAULT_CONFIG["ignored_container_events"]))
        self.assertTrue(watchdog.is_kvision_mariadb_dump_helper_crash(container, attrs, 137))

    def test_builtin_predicate_does_not_hide_other_failures(self):
        attrs = {"name": "zen_hypatia", "image": IMAGE, "exitCode": "137"}
        cases = (
            ("primary MariaDB", helper(name="config_kvision-infra-mariadb_1"),
             {**attrs, "name": "config_kvision-infra-mariadb_1"}, 137),
            ("exit 1", helper(), attrs, 1),
            ("exit 0", helper(), attrs, 0),
            ("not AutoRemove", helper(auto_remove=False), attrs, 137),
            ("wrong mount", helper(mount="/tmp/other.sql.gz"), attrs, 137),
            ("wrong image", helper(image="other_image:1.0"),
             {**attrs, "image": "other_image:1.0"}, 137),
            ("normal unlabeled service", helper(name="ordinary-service",
             image="other_image:1.0"), {"name": "ordinary-service"}, 137),
        )
        for description, container, event, exit_code in cases:
            with self.subTest(case=description):
                self.assertFalse(watchdog.is_kvision_mariadb_dump_helper_crash(
                    container, event, exit_code))

    def test_explicit_verified_label_rule_still_works_when_configured(self):
        rule = [{"label": {"com.example.test.role": "temporary"}, "events": ["die"]}]
        container = helper(labels={"com.example.test.role": "temporary"})
        event = {"name": "zen_hypatia"}
        self.assertTrue(watchdog.is_ignored_container_event(container, event, "die", rule))
        self.assertFalse(watchdog.is_ignored_container_event(container, event, "oom", rule))
        self.assertFalse(watchdog.is_ignored_container_event(helper(), event, "die", rule))
        self.assertFalse(watchdog.is_ignored_container_event(container, event, "die", []))

    def test_runtime_defaults_and_reference_template_have_no_implicit_label_rule(self):
        template = yaml.safe_load((ROOT / "watchdog-config.yaml.example").read_text())
        self.assertEqual(template["ignored_container_events"], [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "watchdog-config.yaml"
            path.write_text("log_level: WARNING\n")
            self.assertEqual(watchdog.load_config(str(path))["ignored_container_events"], [])

            path.write_text(
                "ignored_container_events:\n"
                "  - label:\n"
                "      com.example.test.role: temporary\n"
                "    events: [die]\n"
            )
            self.assertEqual(watchdog.load_config(str(path))["ignored_container_events"],
                             [{"label": {"com.example.test.role": "temporary"},
                               "events": ["die"]}])


if __name__ == "__main__":
    unittest.main()
