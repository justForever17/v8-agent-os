"""Archive-level counterexamples for the Phone native release boundary."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("phone_package", Path(__file__).with_name("verify-phone-executor-package.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def dex_fixture(definitions, references=()):
    strings = sorted(set(definitions) | set(references))
    types = 112 + len(strings) * 4
    classes = types + len(strings) * 4
    string_data = classes + len(definitions) * 32
    data = bytearray(string_data)
    data[:8] = b"dex\n039\0"
    struct.pack_into("<I", data, 36, 112)
    struct.pack_into("<I", data, 40, 0x12345678)
    struct.pack_into("<II", data, 56, len(strings), 112)
    struct.pack_into("<II", data, 64, len(strings), types)
    struct.pack_into("<II", data, 96, len(definitions), classes)
    for index, value in enumerate(strings):
        struct.pack_into("<I", data, 112 + index * 4, len(data))
        struct.pack_into("<I", data, types + index * 4, index)
        assert len(value) < 128
        data.extend(bytes([len(value)]) + value.encode() + b"\0")
    for index, value in enumerate(sorted(definitions)):
        struct.pack_into("<I", data, classes + index * 32, strings.index(value))
    struct.pack_into("<I", data, 32, len(data))
    return bytes(data)


class AndroidPackageBoundary(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, suffix="apk", *, definitions=None, references=(), extras=None, manifest=None):
        archive = self.root / f"fixture.{suffix}"
        manifest = "\0".join(gate.REQUIRED_MANIFEST) if manifest is None else manifest
        definitions = gate.REQUIRED_CLASSES if definitions is None else definitions
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("AndroidManifest.xml" if suffix == "apk" else "base/manifest/AndroidManifest.xml",
                             manifest.encode("utf-16-le" if suffix == "apk" else "utf-8"))
            package.writestr("classes.dex" if suffix == "apk" else "base/dex/classes.dex", dex_fixture(definitions, references))
            for name, value in (extras or {}).items():
                package.writestr(name, value)
        return archive

    def test_apk_and_aab_accept_actual_defined_native_classes(self):
        for suffix in ("apk", "aab"):
            self.assertEqual(gate.verify_archive(self.archive(suffix))["nativeExecutorClasses"], 6)

    def test_class_name_reference_does_not_replace_missing_native_definition(self):
        missing = "Lexpo/modules/v8executor/V8DeviceExecutorModule;"
        with self.assertRaisesRegex(ValueError, "did not compile"):
            gate.verify_archive(self.archive(definitions=gate.REQUIRED_CLASSES - {missing}, references={missing}))

    def test_test_activity_and_fixture_in_other_dex_are_rejected(self):
        for suffix in ("apk", "aab"):
            for forbidden in gate.FORBIDDEN_CLASSES:
                with self.assertRaisesRegex(ValueError, "test or fixture classes"):
                    gate.verify_archive(self.archive(suffix, extras={"feature/dex/classes2.dex": dex_fixture({forbidden})}))

    def test_test_trust_resource_is_rejected_even_when_manifest_does_not_use_it(self):
        for marker in ("v8_executor_test_ca", "v8_executor_test_network_security"):
            with self.assertRaisesRegex(ValueError, "test resources"):
                gate.verify_archive(self.archive(extras={f"res/raw/{marker}.pem": b"synthetic"}))

    def test_test_manifest_registration_and_missing_stop_are_rejected(self):
        clean = "\0".join(gate.REQUIRED_MANIFEST)
        with self.assertRaisesRegex(ValueError, "test components"):
            gate.verify_archive(self.archive(manifest=clean + "\0ExecutorTestActivity"))
        with self.assertRaisesRegex(ValueError, "missing executor"):
            gate.verify_archive(self.archive(manifest=clean.replace("expo.modules.v8executor.ExecutorStopReceiver", "")))

    def test_truncated_dex_is_not_treated_as_a_valid_module(self):
        with self.assertRaisesRegex(ValueError, "DEX"):
            gate.verify_archive(self.archive(extras={"classes2.dex": b"dex\n039\0"}))

    def test_jvm_guard_report_requires_real_passing_named_cases(self):
        report = self.root / "guard.xml"
        cases = "".join(f'<testcase name="{name}"/>' for name in gate.REQUIRED_GUARDS)
        report.write_text(f'<testsuite>{cases}</testsuite>', encoding="utf-8")
        self.assertEqual(gate.verify_junit(report), len(gate.REQUIRED_GUARDS))
        for body in ("", cases + "<testcase><skipped/></testcase>", cases + "<failure/>"):
            report.write_text(f'<testsuite>{body}</testsuite>', encoding="utf-8")
            with self.assertRaises(ValueError):
                gate.verify_junit(report)


if __name__ == "__main__":
    unittest.main()
