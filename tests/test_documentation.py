"""Read-only documentation consistency checks; no Docker or network access."""

import re
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
LINK_RE = re.compile(r"\]\(([^)#]+)?(?:#([a-zA-Z0-9\-]+))?\)")


def heading_anchors(markdown):
    counts = {}
    anchors = set()
    fence = None
    for line in markdown.splitlines():
        marker = line.strip()[:3]
        if marker in ("~~~", chr(96) * 3):
            fence = None if fence == marker else marker
            continue
        if fence:
            continue
        matched = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not matched:
            continue
        slug = re.sub(r"[^\w\- ]", "", matched.group(1).lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug + (f"-{count}" if count else ""))
    if fence:
        raise AssertionError("Unclosed Markdown code fence")
    return anchors


class DocumentationTests(unittest.TestCase):
    def test_all_relative_links_and_headings_resolve(self):
        for doc in DOCS:
            source = doc.read_text()
            heading_anchors(source)
            for match in LINK_RE.finditer(source):
                relative, anchor = match.groups()
                if relative and relative.startswith(("http:", "https:", "mailto:")):
                    continue
                target = (doc.parent / (relative or "")).resolve() if relative else doc
                with self.subTest(document=str(doc.relative_to(ROOT)), link=match.group(0)):
                    self.assertTrue(target.exists(), f"Broken link to {relative}")
                    if anchor and target.suffix == ".md":
                        self.assertIn(anchor, heading_anchors(target.read_text()))

    def test_customer_docs_are_self_contained(self):
        docs = [ROOT / "README.md", *(ROOT / "docs/customer").glob("*.md")]
        for doc in docs:
            for match in LINK_RE.finditer(doc.read_text()):
                relative, _ = match.groups()
                if not relative or relative.startswith(("http:", "https:", "mailto:")):
                    continue
                target = (doc.parent / relative).resolve()
                self.assertNotIn(
                    ROOT / "docs/engineering",
                    target.parents,
                    f"Customer bundle refers to internal engineering file: {doc}",
                )
        self.assertFalse((ROOT / "DEPLOYMENT.md").exists())
        self.assertFalse((ROOT / "docs/DEPLOYMENT_ADVANCED.md").exists())

    def test_release_notes_links_work_when_published_as_github_release(self):
        # GitHub Release notes are rendered at the release URL, not at
        # docs/customer/, so links to other files must be absolute.
        for notes in (ROOT / "docs/customer").glob("RELEASE_NOTES_V*.md"):
            for match in LINK_RE.finditer(notes.read_text()):
                destination, _ = match.groups()
                if not destination or destination.startswith("#"):
                    continue
                with self.subTest(release_notes=notes.name, link=destination):
                    self.assertTrue(
                        destination.startswith(("https:", "http:", "mailto:")),
                        "Relative links are broken when copied into GitHub Releases",
                    )

    def test_operator_guide_covers_configuration_template_fields(self):
        config = yaml.safe_load((ROOT / "watchdog-config.yaml.example").read_text())
        guide = (ROOT / "docs/customer/INSTALLATION_AND_OPERATIONS.md").read_text()

        def keys(item, prefix=""):
            if isinstance(item, dict):
                for key, value in item.items():
                    field = prefix + str(key)
                    yield field
                    yield from keys(value, field + ".")

        for key in keys(config):
            # Detailed event matching and individual custom-probe entries are
            # supported by the operator guide without documenting test fixtures.
            if key.startswith(("ignored_container_events", "container_health_checks")):
                continue
            with self.subTest(yaml_setting=key):
                self.assertIn(key, guide)

        for line in (ROOT / ".env.example").read_text().splitlines():
            matched = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", line)
            if matched:
                with self.subTest(env_setting=matched.group(1)):
                    self.assertIn(matched.group(1), guide)


if __name__ == "__main__":
    unittest.main()
