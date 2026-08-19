from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SHELL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SHELL_ROOT.parents[1]
PROBE = SHELL_ROOT / "scripts" / "feature_pack_runtime_probe.py"


def load_probe_module():
    spec = importlib.util.spec_from_file_location("v8os_feature_pack_runtime_probe", PROBE)
    if spec is None or spec.loader is None:
        raise RuntimeError("feature pack probe module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FeaturePackRuntimeProbeTest(unittest.TestCase):
    def run_probe(self, payload: object, *, environment: dict[str, str] | None = None):
        env = dict(os.environ)
        env.update(environment or {})
        return subprocess.run(
            [sys.executable, "-I", "-B", str(PROBE)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )

    def parse_single_line(self, process: subprocess.CompletedProcess[str]) -> dict:
        self.assertEqual(process.stderr, "")
        lines = process.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_clean_state_is_checked_fail_closed_without_persisting_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary)
            config_path = state_root / "config.json"
            config_path.write_text('{"probeSentinel":true}', encoding="utf-8")
            config_before = config_path.read_bytes()
            process = self.run_probe(
                {
                    "engineStatus": {
                        "featurePacks": [
                            {"id": "rpa_automation", "status": "not_installed", "installed": False},
                            {"id": "creative_media_image_analysis", "status": "not_installed", "installed": False},
                            {"id": "document_ingestion", "status": "not_installed", "installed": False},
                        ]
                    }
                },
                environment={
                    "V8_REPO_ROOT": str(REPO_ROOT),
                    "V8_AGENT_OS_HOME": str(state_root),
                },
            )

            response = self.parse_single_line(process)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(response["mode"], "offline_runtime_probe")
            self.assertTrue(response["ok"])
            self.assertEqual(response["error"], None)
            self.assertEqual(response["rpa"], {"state": "not_installed", "failClosed": True, "checked": True, "error": None})
            self.assertEqual(response["image"], {"state": "not_installed", "failClosed": True, "checked": True, "error": None})
            self.assertEqual(response["documents"], {"state": "not_installed", "failClosed": True, "checked": True, "error": None})
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertFalse((state_root / "runtime_registry.json").exists())

    def test_invalid_engine_status_is_stable_input_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            process = self.run_probe(
                {"engineStatus": {"featurePacks": {}}},
                environment={
                    "V8_REPO_ROOT": str(REPO_ROOT),
                    "V8_AGENT_OS_HOME": temporary,
                },
            )

        response = self.parse_single_line(process)
        self.assertEqual(process.returncode, 2)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "engine_status_invalid")
        self.assertNotIn(str(REPO_ROOT), process.stdout)

    def test_installed_rpa_probe_requires_isolated_excel_robot_dry_run(self) -> None:
        module = load_probe_module()
        observed: dict[str, str] = {}

        class FakeAdapter:
            def availability(self):
                return {
                    "robotFramework": True,
                    "rpaFramework": True,
                    "libraries": {
                        "RPA.Excel.Files": True,
                        "RPA.Windows": False,
                    },
                }

            def validate_robot_file(self, *, robot_file: Path, output_dir: Path):
                observed["source"] = robot_file.read_text(encoding="utf-8")
                observed["output"] = output_dir.name
                return {"passed": True, "command": [sys.executable, "-I", "-c", "probe"]}

        result, ok = module._probe_rpa(
            FakeAdapter,
            {"status": "installed", "installed": True, "restartRequired": False},
        )

        self.assertTrue(ok)
        self.assertTrue(result["available"])
        self.assertTrue(result["isolated"])
        self.assertTrue(result["dryRunPassed"])
        self.assertIn("Library    RPA.Excel.Files", observed["source"])
        self.assertIn("Create Workbook", observed["source"])
        self.assertEqual(observed["output"], "dryrun")

    def test_installed_image_probe_requires_resolved_asset_and_cpu_session(self) -> None:
        module = load_probe_module()

        with tempfile.TemporaryDirectory() as temporary:
            asset = Path(temporary) / "model.onnx"
            asset.write_bytes(b"governed-model")
            target = Path(temporary) / "python"
            target.mkdir()
            observed: dict[str, object] = {}

            def resolve_asset(pack_id: str, asset_id: str):
                observed["identity"] = (pack_id, asset_id)
                return asset

            def probe_runtime(asset_path: Path, target_dir: str):
                observed["load"] = (asset_path.name, Path(target_dir).name)
                return {
                    "cpuSessionLoaded": True,
                    "isolated": True,
                    "moduleOriginsVerified": True,
                    "modelShaVerified": True,
                }

            result, ok = module._probe_image(
                resolve_asset,
                probe_runtime,
                {
                    "status": "installed",
                    "installed": True,
                    "restartRequired": False,
                    "targetDir": str(target),
                },
            )

        self.assertTrue(ok)
        self.assertTrue(result["assetResolved"])
        self.assertTrue(result["cpuSessionLoaded"])
        self.assertTrue(result["isolated"])
        self.assertTrue(result["moduleOriginsVerified"])
        self.assertTrue(result["modelShaVerified"])
        self.assertEqual(
            observed["identity"],
            ("creative_media_image_analysis", "isnet_general_use"),
        )
        self.assertEqual(observed["load"][0], "model.onnx")
        self.assertEqual(observed["load"][1], "python")

    def test_installed_document_probe_requires_isolated_pack_origins_and_parser_checks(self) -> None:
        module = load_probe_module()

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "python"
            target.mkdir()

            class FakeModule:
                def __init__(self, name: str):
                    self.__file__ = str(target / name / "__init__.py")

            observed: dict[str, object] = {}

            def exercise(modules, *, native_reader=None):
                observed["modules"] = tuple(modules)
                observed["nativeReader"] = native_reader
                return True, True

            result, ok = module._probe_documents(
                {
                    "status": "installed",
                    "installed": True,
                    "restartRequired": False,
                    "targetDir": str(target),
                },
                import_module=lambda name: FakeModule(name),
                exercise_parsers=exercise,
                native_reader=lambda _path: "verified",
                isolated_runtime=True,
            )

        self.assertTrue(ok)
        self.assertTrue(result["available"])
        self.assertTrue(result["isolated"])
        self.assertTrue(result["moduleOriginsVerified"])
        self.assertTrue(result["parsersVerified"])
        self.assertTrue(result["nativeToolVerified"])
        self.assertEqual(observed["modules"], module.DOCUMENT_MODULE_NAMES)
        self.assertIsNotNone(observed["nativeReader"])

    def test_installed_document_probe_accepts_dependency_from_isolated_runtime(self) -> None:
        module = load_probe_module()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "feature-pack" / "python"
            runtime = root / "embedded-python"
            target.mkdir(parents=True)
            runtime.mkdir()

            class FakeModule:
                def __init__(self, origin: Path):
                    self.__file__ = str(origin / "module.py")

            def import_module(name: str):
                origin = runtime if name == "tabulate" else target
                return FakeModule(origin)

            result, ok = module._probe_documents(
                {
                    "status": "installed",
                    "installed": True,
                    "restartRequired": False,
                    "targetDir": str(target),
                },
                import_module=import_module,
                exercise_parsers=lambda _modules, native_reader=None: (True, True),
                isolated_runtime=True,
                trusted_runtime_roots=(runtime,),
            )

        self.assertTrue(ok)
        self.assertTrue(result["moduleOriginsVerified"])

    def test_installed_document_probe_rejects_dependency_outside_governed_roots(self) -> None:
        module = load_probe_module()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "feature-pack" / "python"
            runtime = root / "embedded-python"
            external = root / "external-site"
            target.mkdir(parents=True)
            runtime.mkdir()
            external.mkdir()

            class FakeModule:
                def __init__(self, origin: Path):
                    self.__file__ = str(origin / "module.py")

            def import_module(name: str):
                return FakeModule(external if name == "tabulate" else target)

            result, ok = module._probe_documents(
                {
                    "status": "installed",
                    "installed": True,
                    "restartRequired": False,
                    "targetDir": str(target),
                },
                import_module=import_module,
                exercise_parsers=lambda _modules, native_reader=None: (True, True),
                isolated_runtime=True,
                trusted_runtime_roots=(runtime,),
            )

        self.assertFalse(ok)
        self.assertFalse(result["moduleOriginsVerified"])


if __name__ == "__main__":
    unittest.main()
