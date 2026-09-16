"""The state owner is a process boundary, not a project construction lock."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading

from core.engine_instance import engine_state_owner
from core.process_launch import popen_windowless

ENGINE_ROOT = Path(__file__).resolve().parents[2]


def _child(home):
    process = popen_windowless([sys.executable, '-c', '''
import sys
from pathlib import Path
from core.engine_instance import engine_state_owner, EngineStateInUse
try:
    with engine_state_owner(Path(sys.argv[1])):
        print('acquired')
except EngineStateInUse:
    print('already_owned')
    sys.exit(23)
''', str(home)], cwd=str(ENGINE_ROOT), stdout=-1, stderr=-1, text=True)
    output, error = process.communicate(timeout=10)
    return process.returncode, output.strip(), error


def test_same_root_rejected_other_root_allowed_and_project_workers_overlap(tmp_path):
    state_root = tmp_path / 'state'
    workspace = tmp_path / 'project'
    workspace.mkdir()
    barrier = threading.Barrier(4)

    def worker(index):
        # All workers must reach the same point while Engine owns its state.
        barrier.wait(timeout=5)
        path = workspace / f'part-{index}.txt'
        path.write_text(str(index), encoding='utf-8')
        return path.read_text(encoding='utf-8')

    with engine_state_owner(state_root):
        rejected = _child(state_root)
        assert rejected[:2] == (23, 'already_owned'), rejected
        other = _child(tmp_path / 'other-state')
        assert other[:2] == (0, 'acquired'), other
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(worker, range(4))) == ['0', '1', '2', '3']
    reacquired = _child(state_root)
    assert reacquired[:2] == (0, 'acquired'), reacquired


def test_process_crash_releases_owner_without_deleting_coordination_file(tmp_path):
    process = popen_windowless([sys.executable, '-c', '''
import os, sys
from pathlib import Path
from core.engine_instance import engine_state_owner
with engine_state_owner(Path(sys.argv[1])):
    os._exit(17)
''', str(tmp_path)], cwd=str(ENGINE_ROOT), stdout=-1, stderr=-1, text=True)
    process.communicate(timeout=10)
    assert process.returncode == 17
    assert (tmp_path / 'runtime' / 'engine-instance.lock').exists()
    assert _child(tmp_path)[:2] == (0, 'acquired')
