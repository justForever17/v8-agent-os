"""Exercise the public boundary with a real isolated Git index."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from audit_public_docs import boundary_violations, link_violations, tracked_files


class PublicDocsBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.write(".gitignore", "docs/**\n!docs/guide.md\n!docs/assets/\n!docs/assets/banner.svg\n")
        self.write(".gitattributes", "docs/private export-ignore\nreports export-ignore\n")
        self.write("docs/guide.md", "# Guide\n\n## Connection\n")
        self.write("docs/assets/banner.svg", "<svg/>\n")
        self.write("README.md", "[Guide](docs/guide.md#connection)\n![Brand](docs/assets/banner.svg)\n")
        self.git("add", ".")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_public_guide_and_brand_asset_pass(self):
        tracked = tracked_files(self.root)
        self.assertEqual(boundary_violations(self.root, tracked), [])
        self.assertEqual(link_violations(self.root, tracked), [])

    def test_forced_internal_document_is_rejected(self):
        self.write("docs/private/report.md", "Internal fixture\n")
        self.git("add", "-f", "docs/private/report.md")
        violations = boundary_violations(self.root, tracked_files(self.root))
        self.assertIn("docs/private/report.md:tracked_outside_public_allowlist", violations)

    def test_generated_report_is_rejected_even_outside_docs(self):
        self.write("reports/result.json", "{}\n")
        self.git("add", "reports/result.json")
        self.assertIn("reports/result.json:tracked_local_only_material", boundary_violations(self.root, tracked_files(self.root)))

    def test_local_backup_cannot_mask_unpublished_link(self):
        self.write("docs/private/report.md", "# Local only\n")
        self.write("README.md", "[Report](docs/private/report.md)\n")
        self.assertIn("README.md:unpublished_link:docs/private/report.md", link_violations(self.root, tracked_files(self.root)))

    def test_renamed_heading_is_rejected_but_code_example_is_not_a_link(self):
        self.write("README.md", "[Old](docs/guide.md#removed)\n```md\n[Example](absent.md)\n```\n")
        self.assertEqual(link_violations(self.root, tracked_files(self.root)), ["README.md:missing_anchor:docs/guide.md#removed"])

    def test_root_and_html_links_cannot_republish_private_targets(self):
        self.write("docs/private/report.md", "# Local only\n")
        self.write("README.md", '[Report](/docs/private/report.md)\n<a href="docs/private/report.md">Report</a>\n')
        self.assertEqual(len(link_violations(self.root, tracked_files(self.root))), 2)

    def test_root_links_check_directories_uploads_and_encoded_paths(self):
        self.write("README.md", '[Report](/scripts/experience/9131/reports/)\n<a href="/apps/example/public/user/private.webp">Upload</a>\n[Encoded](/%64ocs/private/report.html)\n')
        self.assertEqual(len(link_violations(self.root, tracked_files(self.root))), 3)

    def test_uploaded_avatar_is_rejected_without_excluding_default_art(self):
        self.write(".gitignore", "apps/example/public/Avatar/*\n!apps/example/public/Avatar/default.svg\n")
        self.write("apps/example/public/Avatar/default.svg", "<svg/>\n")
        self.write("apps/example/public/Avatar/private.webp", "synthetic upload\n")
        self.git("add", "apps/example/public/Avatar/default.svg")
        self.assertEqual(boundary_violations(self.root, tracked_files(self.root)), [])
        self.git("add", "-f", "apps/example/public/Avatar/private.webp")
        self.assertEqual(boundary_violations(self.root, tracked_files(self.root)), ["apps/example/public/Avatar/private.webp:tracked_outside_public_allowlist"])


if __name__ == "__main__":
    unittest.main()
