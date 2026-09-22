"""Offline distribution contracts; all Git/install state lives in temporary fixtures."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("server_bundle", SERVER / "build-server-bundle.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="server-distribution-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.original_root = builder.ROOT
        builder.ROOT = self.repo
        self.addCleanup(setattr, builder, "ROOT", self.original_root)
        self.files = {
            "release-manifest.json": json.dumps({"release": {"version": "2026.09.16.4"}, "products": {"server": {"targets": {"linux-x64": {"enabled": True}, "linux-arm64": {"enabled": False}}}}}),
            "LICENSE": "synthetic license", "VERSION": "2026.09.16.4",
            "apps/v8-agent-os-engine/main.py": "print('tracked')\n",
            "apps/v8-agent-os-engine/requirements/server-linux-x64.lock": "fixture==1 --hash=sha256:" + "1" * 64,
            "apps/v8-agent-os-cli/bin/v8os.mjs": "console.log('fixture')\n",
            "scripts/server/install.sh": "#!/bin/bash\necho fixture\n",
            "scripts/server/v8os": "#!/bin/bash\necho fixture\n",
            "scripts/server/verify_server.py": "print('fixture')\n",
            "scripts/server/README.md": "synthetic README\n",
            "scripts/server/build-feature-packs.mjs": "import fs from 'node:fs'; fs.writeFileSync(process.argv[2], '// fixture compiled from tracked inputs\\n');\n",
            "apps/v8-agent-os-web/src/admin/lib/server/tracked.ts": "export const tracked = true;\n",
        }
        for name, text in self.files.items():
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf8")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        (self.repo / "scripts/server/node_modules").mkdir()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, stderr=subprocess.PIPE)

    def read_bundle(self, result):
        with tarfile.open(result["asset"]) as archive:
            return {member.name.split("/", 1)[1]: archive.extractfile(member).read() for member in archive if member.isfile()}

    def test_untracked_production_files_never_enter_archive(self):
        unexpected = self.repo / "apps/v8-agent-os-engine/private-diagnostic.json"
        unexpected.write_text('{"synthetic":"must not ship"}')
        result = builder.build(self.root / "out")
        files = self.read_bundle(result)
        self.assertNotIn("apps/v8-agent-os-engine/private-diagnostic.json", files)
        self.assertFalse(json.loads(files["server-manifest.json"])["sourceDirty"])

    def test_dirty_build_requires_explicit_candidate_and_is_marked(self):
        (self.repo / "apps/v8-agent-os-engine/main.py").write_text("print('candidate')\n")
        with self.assertRaisesRegex(ValueError, "--allow-dirty"):
            builder.build(self.root / "strict")
        files = self.read_bundle(builder.build(self.root / "candidate", allow_dirty=True))
        self.assertTrue(json.loads(files["server-manifest.json"])["sourceDirty"])
        self.assertIn(b"candidate", files["apps/v8-agent-os-engine/main.py"])

    def test_enabled_arm_target_is_rejected_before_false_x64_success(self):
        manifest = json.loads((self.repo / "release-manifest.json").read_text())
        manifest["products"]["server"]["targets"]["linux-arm64"]["enabled"] = True
        (self.repo / "release-manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "unimplemented targets"):
            builder.build(self.root / "out", allow_dirty=True)

    def test_untracked_typescript_dependency_cannot_enter_through_compiler(self):
        (self.repo / "scripts/server/build-feature-packs.mjs").write_text("import './untracked.mjs';\n")
        (self.repo / "scripts/server/untracked.mjs").write_text("process.exit(0);\n")
        with self.assertRaises(subprocess.CalledProcessError):
            builder.build(self.root / "out", allow_dirty=True)
        self.assertEqual(list((self.root / "out").glob("*.tar.gz")), [])

    def test_identical_commit_builds_identical_archive_bytes(self):
        first = builder.build(self.root / "one")
        second = builder.build(self.root / "two")
        self.assertEqual(first["sha256"], second["sha256"])
        files = self.read_bundle(first)
        for line in files["SHA256SUMS"].decode().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest)


@unittest.skipUnless(sys.platform == "linux", "installer fault fixture requires Linux shell/flock")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="server-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle with spaces"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.bundle.mkdir()
        self.engine = self.bundle / "apps/v8-agent-os-engine"
        (self.engine / "requirements").mkdir(parents=True)
        (self.bundle / "scripts/server").mkdir(parents=True)
        (self.bundle / "install.sh").write_text((SERVER / "install.sh").read_text(), newline="\n")
        (self.engine / "requirements/server-linux-x64.lock").write_text("synthetic lock\n")
        (self.bundle / "scripts/server/verify_server.py").write_text("synthetic verifier\n")
        sums = [f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(self.bundle)}" for file in self.bundle.rglob("*") if file.is_file()]
        (self.bundle / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        self.log = self.root / "calls"
        self.write_command("id", "#!/bin/sh\necho 1001\n")
        self.write_command("node", "#!/bin/sh\nexit 0\n")
        # This is the external Python/pip/download boundary. The shell installer,
        # checksum check, receipt, retry and real file operations remain intact.
        self.write_command("python3.11", f'''#!{sys.executable}
import os,sys,pathlib
args=sys.argv[1:]
with open(os.environ['FIXTURE_CALLS'],'a') as out: out.write(' '.join(args)+'\\n')
if args[:2]==['-m','venv']:
    dest=pathlib.Path(args[2])/'bin';dest.mkdir(parents=True,exist_ok=True)
    target=dest/'python3'
    if not target.exists():target.symlink_to(pathlib.Path(__file__).resolve())
if args[:3]==['-m','pip','install'] and os.environ.get('FIXTURE_FAIL')=='pip':sys.exit(31)
if args[:3]==['-m','playwright','install'] and os.environ.get('FIXTURE_FAIL')=='browser':sys.exit(32)
''')
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FIXTURE_CALLS": str(self.log)}
        self.env.pop("V8_SERVER_PYTHON", None)

    def write_command(self, name, content):
        script = self.bin / name
        script.write_text(content)
        script.chmod(0o755)

    def install(self, fail=""):
        return subprocess.run(["bash", str(self.bundle / "install.sh")], env={**self.env, "FIXTURE_FAIL": fail}, text=True, capture_output=True)

    def test_failed_dependency_download_can_resume_same_bundle(self):
        first = self.install("pip")
        self.assertNotEqual(first.returncode, 0)
        self.assertTrue((self.engine / ".venv").exists())
        resumed = self.install()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn("rerun this install.sh", first.stderr)
        self.assertTrue((self.bundle / ".server-install-state").read_text().endswith(" complete\n"))

    def test_browser_failure_resumes_without_reinstalling_dependencies(self):
        first = self.install("browser")
        self.assertNotEqual(first.returncode, 0)
        first_install_calls = self.log.read_text().count("-m pip install")
        resumed = self.install()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.log.read_text().count("-m pip install"), first_install_calls)

    def test_existing_unmanaged_venv_is_never_adopted(self):
        (self.engine / ".venv").mkdir()
        sentinel = self.engine / ".venv/user-owned"
        sentinel.write_text("keep")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertNotIn("-m pip install", self.log.read_text())

    def test_source_change_blocks_resume_before_downloading(self):
        self.install("pip")
        before = self.log.read_text()
        (self.engine / "requirements/server-linux-x64.lock").write_text("tampered")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log.read_text().count("-m pip install"), before.count("-m pip install"))

    def test_completed_install_rechecks_without_rewriting_dependencies(self):
        self.assertEqual(self.install().returncode, 0)
        before = self.log.read_text().count("-m pip install")
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.log.read_text().count("-m pip install"), before)

    def test_concurrent_install_is_rejected_without_package_mutation(self):
        import fcntl
        with (self.bundle / ".server-install.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.install()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Another server installation", result.stderr)
            self.assertFalse((self.engine / ".venv").exists())


if __name__ == "__main__":
    unittest.main()
