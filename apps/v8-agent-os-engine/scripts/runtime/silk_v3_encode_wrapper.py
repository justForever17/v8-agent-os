from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _candidate_commands(tool_root: Path) -> list[list[str]]:
    candidates: list[list[str]] = []
    wrapper_cmd = tool_root / "bin" / "silk_v3_encoder.cmd"
    wrapper_bat = tool_root / "bin" / "silk_v3_encoder.bat"
    wrapper_py = tool_root / "bin" / "silk_v3_encoder.py"
    direct_exe = tool_root / "bin" / "silk_v3_encoder.exe"

    if wrapper_cmd.exists():
        candidates.append([str(wrapper_cmd)])
    if wrapper_bat.exists():
        candidates.append([str(wrapper_bat)])
    if wrapper_py.exists():
        candidates.append([sys.executable, str(wrapper_py)])
    if direct_exe.exists():
        candidates.append([str(direct_exe)])
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="V8Chat Silk V3 编码包装层")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-rate", required=True, type=int)
    parser.add_argument("--bitrate", required=True, type=int)
    parser.add_argument("--tool-root", required=True)
    args = parser.parse_args()

    tool_root = Path(args.tool_root).expanduser()
    candidates = _candidate_commands(tool_root)
    if not candidates:
        print(
            f"Silk toolchain missing under {tool_root}. "
            "Expected one of bin/silk_v3_encoder.cmd|.bat|.py|.exe.",
            file=sys.stderr,
        )
        return 2

    last_error = ""
    for command in candidates:
        full_command = [
            *command,
            "--input",
            args.input,
            "--output",
            args.output,
            "--sample-rate",
            str(args.sample_rate),
            "--bitrate",
            str(args.bitrate),
        ]
        result = subprocess.run(full_command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode == 0 and Path(args.output).exists():
            return 0
        last_error = str(result.stderr or "").strip() or f"encoder exited with code {result.returncode}"

    print(last_error or "Silk encoder failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
