"""Prove the host oracle kills a removed device-side epoch fence, in a copy."""
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v8-executor-mutant-") as folder:
        root = Path(folder)
        shutil.copytree(source / "main", root / "main", ignore=shutil.ignore_patterns("main.c", "provision.*", "idf_component.yml", "Kconfig.projbuild", "CMakeLists.txt"))
        shutil.copytree(source / "tests", root / "tests")
        target = root / "main" / "executor_core.c"
        original = target.read_text()
        fence = "c->epoch != e->epoch || "
        assert original.count(fence) == 1
        target.write_text(original.replace(fence, ""))
        subprocess.run(["cmake", "-S", str(root / "tests"), "-B", str(root / "build"), "-G", "Ninja"], check=True, capture_output=True)
        subprocess.run(["cmake", "--build", str(root / "build"), "-j2"], check=True, capture_output=True)
        result = subprocess.run([str(root / "build" / "executor_core_test")], capture_output=True, text=True)
        assert result.returncode != 0 and "b.writes == 0" in result.stderr, result.stdout + result.stderr
        print("Killed removed-epoch mutant: old epoch attempted a GPIO write; unchanged behavior oracle rejected it.")


if __name__ == "__main__":
    main()
