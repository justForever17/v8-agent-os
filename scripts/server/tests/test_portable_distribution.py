"""Portable archive contracts; synthetic fixtures never read real V8OS state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "build-engine-bundle.py"
spec = importlib.util.spec_from_file_location("engine_bundle", SOURCE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PortableBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable-distribution-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "source with spaces"
        self.engine = self.bundle / "apps/v8-agent-os-engine"
        self.server = {
            "schema": 1, "profile": "server", "version": "2026.09.22.1", "platform": "linux", "arch": "x64",
            "sourceCommit": "a" * 40, "sourceDirty": False, "engine": "apps/v8-agent-os-engine",
            "cli": "apps/v8-agent-os-cli/bin/v8os.mjs",
        }
        source = {
            "server-manifest.json": json.dumps(self.server), "VERSION": "2026.9.22-1\n",
            "apps/v8-agent-os-engine/main.py": "print('source')\n",
            "apps/v8-agent-os-cli/bin/v8os.mjs": "console.log('cli')\n",
            "apps/v8-agent-os-engine/requirements/server-linux-x64.lock": "fixture==1\n",
            "install.sh": "#!/bin/sh\nexit 0\n", "v8os": "#!/bin/sh\nexit 0\n",
        }
        for relative, text in source.items():
            file = self.bundle / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf8")
        self.refresh_checksums()
        self.runtime_receipt = {
            "schema": 1, "profile": "server", "target": "linux-x64", "browserIncluded": True,
            "requirementsSha256": builder.sha256(self.engine / "requirements/server-linux-x64.lock"),
        }
        for relative in (".python/bin/python3", ".python/bin/python3.11", ".python/lib/playwright/driver/node", ".playwright-browsers/chromium-1/chrome-linux/chrome"):
            file = self.engine / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("#!/bin/sh\nprintf PORTABLE_EXEC_OK\n", encoding="utf8")
            file.chmod(0o755)
        self.write_receipt()

    def refresh_checksums(self):
        (self.bundle / "SHA256SUMS").write_text("\n".join(
            f"{builder.sha256(file)}  {file.relative_to(self.bundle).as_posix()}"
            for file in sorted(self.bundle.rglob("*")) if file.is_file() and file.name != "SHA256SUMS"
        ) + "\n", encoding="utf8")

    def write_receipt(self):
        (self.engine / ".python/v8os-runtime.json").write_text(json.dumps(self.runtime_receipt), encoding="utf8")

    def build(self, **kwargs):
        return builder.build(self.bundle, self.root / "out", **kwargs)

    def test_archive_metadata_preserves_execute_bits_and_strips_special_bits(self):
        for original, expected in ((0o4755, 0o755), (0o700, 0o755), (0o644, 0o644)):
            entry = tarfile.TarInfo("bundle/.python/bin/python3.11")
            entry.mode = original
            self.assertEqual(builder.archive_metadata(entry).mode, expected)

    @unittest.skipUnless(sys.platform == "linux", "executable archive permissions require POSIX")
    def test_every_native_executable_keeps_permission_not_just_python_alias(self):
        result = self.build()
        with tarfile.open(result["asset"]) as archive:
            for suffix in ("/bin/python3.11", "/driver/node", "/chrome-linux/chrome"):
                member = next(item for item in archive if item.name.endswith(suffix))
                self.assertEqual(member.mode, 0o755, suffix)
            main = next(item for item in archive if item.name.endswith("/main.py"))
            self.assertEqual(main.mode, 0o644)
            self.assertFalse(any(item.name.endswith(("/install.sh", "/v8os")) for item in archive))
            archive.extractall(self.root / "extracted", filter="data")
        if sys.platform == "linux":
            chrome = next((self.root / "extracted").rglob("chrome"))
            self.assertEqual(subprocess.check_output([str(chrome)]), b"PORTABLE_EXEC_OK")

    def test_manifest_is_in_checksum_closure_and_matches_public_identity(self):
        result = self.build()
        public = json.loads(Path(result["manifest"]).read_text())
        self.assertEqual(public["sha256"], builder.sha256(Path(result["asset"])))
        with tarfile.open(result["asset"]) as archive:
            files = {member.name.split("/", 1)[1]: archive.extractfile(member).read() for member in archive if member.isfile()}
        sums = dict(line.split("  ", 1)[::-1] for line in files["SHA256SUMS"].decode().splitlines())
        self.assertEqual(sums["engine-manifest.json"], hashlib.sha256(files["engine-manifest.json"]).hexdigest())
        inner = json.loads(files["engine-manifest.json"])
        for key in ("version", "target", "profile", "sourceCommit"):
            self.assertEqual(inner[key], public[key])

    def test_stale_or_relabelled_identity_cannot_be_published(self):
        for override in ({"version": "2026.09.23.1"}, {"source_commit": "b" * 40}):
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, "identity override"):
                self.build(**override)

    def test_modified_source_or_manifest_is_rejected(self):
        (self.engine / "main.py").write_text("print('unexpected change')\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.build()

    def test_semver_projection_must_represent_the_same_release(self):
        version = self.bundle / "VERSION"
        version.write_text("2026.9.21-1\n")
        lines = (self.bundle / "SHA256SUMS").read_text().splitlines()
        (self.bundle / "SHA256SUMS").write_text("\n".join(
            f"{builder.sha256(version)}  VERSION" if line.endswith("  VERSION") else line for line in lines
        ) + "\n")
        with self.assertRaisesRegex(ValueError, "VERSION does not match"):
            self.build()

    def test_unidentified_file_cannot_hitchhike_in_runtime_asset(self):
        (self.bundle / "private-debug.json").write_text('{"synthetic":"must not ship"}')
        with self.assertRaisesRegex(ValueError, "Unidentified file"):
            self.build()

    def test_wrong_dependency_receipt_and_browser_omission_are_rejected(self):
        for changes in ({"requirementsSha256": "0" * 64}, {"target": "linux-arm64"}, {"browserIncluded": False}):
            original = self.runtime_receipt.copy()
            self.runtime_receipt.update(changes)
            self.write_receipt()
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "dependency/browser closure"):
                self.build()
            self.runtime_receipt = original

    @unittest.skipUnless(sys.platform == "linux", "portable symlink/execute semantics require POSIX")
    def test_relative_python_symlink_survives_archive_and_escape_is_rejected(self):
        alias = self.engine / ".python/bin/python3"
        alias.unlink()
        alias.symlink_to("python3.11")
        result = self.build()
        with tarfile.open(result["asset"]) as archive:
            archive.extractall(self.root / "relocated", filter="data")
        relocated = next((self.root / "relocated").rglob("python3"))
        self.assertTrue(relocated.is_symlink())
        self.assertEqual(subprocess.check_output([str(relocated)]), b"PORTABLE_EXEC_OK")
        alias.unlink()
        alias.symlink_to("/usr/bin/python3")
        with self.assertRaisesRegex(ValueError, "escapes package"):
            builder.build(self.bundle, self.root / "invalid")

    def test_existing_sidecar_and_asset_cannot_be_overwritten(self):
        out = self.root / "out"
        out.mkdir()
        sidecar = out / f"V8OS-Engine-{self.server['version']}-linux-x64.json"
        sidecar.write_text("preserve")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(sidecar.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
