import json
import os
import subprocess
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]


def test_v8_agent_os_home_environment_controls_all_canonical_roots(tmp_path):
    configured_home = (tmp_path / "isolated-v8os-home").resolve()
    env = {
        **os.environ,
        "PYTHONPATH": str(ENGINE_ROOT),
        "V8_AGENT_OS_HOME": str(configured_home),
    }
    payload = subprocess.check_output(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from core.v8_agent_os_paths import "
                "CONFIG_JSON_PATH, RUNTIME_DATA_HOME, V8_AGENT_OS_HOME; "
                "print(json.dumps({"
                "'home': str(V8_AGENT_OS_HOME), "
                "'config': str(CONFIG_JSON_PATH), "
                "'runtime': str(RUNTIME_DATA_HOME)"
                "}))"
            ),
        ],
        cwd=ENGINE_ROOT,
        env=env,
        text=True,
    )
    resolved = json.loads(payload)

    assert Path(resolved["home"]) == configured_home
    assert Path(resolved["config"]) == configured_home / "config.json"
    assert Path(resolved["runtime"]) == configured_home / "runtime-data"
