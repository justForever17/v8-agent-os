import importlib.util
from pathlib import Path
import subprocess
import sys


SOURCE = Path(__file__).with_name("run_cross_graph_live_acceptance.py")


def test_live_flag_is_required_before_importing_engine_or_creating_state(tmp_path):
    absent = tmp_path / "uncreated"
    result = subprocess.run([sys.executable, str(SOURCE), "--engine-url", "http://127.0.0.1:22930",
        "--web-url", "http://127.0.0.1:22927", "--state-root", str(absent), "--output", str(absent / "report.json")],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--live and --allow-side-effects" in result.stderr
    assert not absent.exists()


def test_completion_after_the_worker_finishes_is_not_concurrent_parent_work():
    spec = importlib.util.spec_from_file_location("cross_graph_live", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = module.parent_write_during_episode
    assert observed(15, [(10, 20)])
    assert not observed(25, [(10, 20)])
    assert not observed(20, [(10, 20)])
    assert not observed(5, [(10, 20)])
    assert not observed(15, [])
