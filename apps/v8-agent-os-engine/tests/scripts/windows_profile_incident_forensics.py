from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WINDOWS_PROFILE_EVENT_PROVIDERS = [
    "Microsoft-Windows-User Profiles Service",
]
RISK_KEYWORDS = [
    "ntuser.dat",
    "usrclass.dat",
    "profilelist",
    "profileimagepath",
    "user shell folders",
    "shell folders",
    "icacls",
    "takeown",
    "mklink",
    "subst",
    "fsutil",
    "robocopy",
    "startup",
]


def parse_timestamp(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_record_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # V8 records use both seconds and milliseconds in different ledgers.
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parse_timestamp(text)
    except Exception:
        return None


def _in_window(record_time: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if record_time is None:
        return True
    if start and record_time < start:
        return False
    if end and record_time > end:
        return False
    return True


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _record_matches_risk(value: dict[str, Any]) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str).lower()
    return any(keyword in text for keyword in RISK_KEYWORDS)


def discover_sqlite_files(runtime_root: Path) -> list[Path]:
    if not runtime_root.exists():
        return []
    candidates: list[Path] = []
    preferred_names = {"v8chat.db", "v8-agent-os.db", "runtime.db", "storage.db"}
    for path in runtime_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        lowered = str(path).lower()
        if "\\cache\\" in lowered or "/cache/" in lowered or "\\backups\\" in lowered or "/backups/" in lowered:
            continue
        if path.name.lower() in preferred_names:
            candidates.insert(0, path)
        else:
            candidates.append(path)
        if len(candidates) >= 16:
            break
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def collect_v8_sqlite_evidence(runtime_root: Path, start: datetime | None, end: datetime | None, *, limit: int = 250) -> dict[str, Any]:
    evidence: dict[str, Any] = {"runtimeRoot": str(runtime_root), "databases": []}
    for db_path in discover_sqlite_files(runtime_root):
        db_entry: dict[str, Any] = {"path": str(db_path), "tables": []}
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            db_entry["unavailable"] = str(exc)
            evidence["databases"].append(db_entry)
            continue
        try:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            for table_row in table_rows:
                table = str(table_row["name"])
                if table.startswith("sqlite_"):
                    continue
                columns = [str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                if not columns:
                    continue
                interesting = any(token in table.lower() for token in ["run", "event", "ledger", "approval", "safety", "tool"])
                if not interesting:
                    continue
                time_columns = [
                    column
                    for column in columns
                    if column.lower() in {"created_at", "createdat", "updated_at", "updatedat", "timestamp", "time", "ts", "occurred_at"}
                ]
                select_columns = ", ".join(f'"{column}"' for column in columns[:24])
                rows = conn.execute(f'SELECT {select_columns} FROM "{table}" LIMIT ?', (limit,)).fetchall()
                matched: list[dict[str, Any]] = []
                for row in rows:
                    payload = {key: _json_safe(row[key]) for key in row.keys()}
                    record_time = None
                    for column in time_columns:
                        record_time = _coerce_record_timestamp(payload.get(column))
                        if record_time is not None:
                            break
                    if not _in_window(record_time, start, end):
                        continue
                    if _record_matches_risk(payload):
                        matched.append(payload)
                if matched:
                    db_entry["tables"].append({"table": table, "matchedRows": matched[:limit], "columns": columns[:24]})
        except Exception as exc:
            db_entry["error"] = str(exc)
        finally:
            conn.close()
        evidence["databases"].append(db_entry)
    return evidence


def run_powershell_json(script: str, *, timeout: int = 30) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if completed.returncode != 0:
        return {"available": False, "returncode": completed.returncode, "stderr": completed.stderr[-4000:]}
    stdout = completed.stdout.strip()
    if not stdout:
        return {"available": True, "items": []}
    try:
        return {"available": True, "data": json.loads(stdout)}
    except Exception:
        return {"available": True, "raw": stdout[-12000:]}


def collect_windows_profile_events(start: datetime | None, end: datetime | None) -> dict[str, Any]:
    start_text = (start or datetime(1970, 1, 1, tzinfo=timezone.utc)).isoformat()
    end_text = (end or datetime.now(timezone.utc)).isoformat()
    script = f"""
$ErrorActionPreference = 'Continue'
$start = [datetime]::Parse('{start_text}')
$end = [datetime]::Parse('{end_text}')
$app = @()
try {{
  $app = Get-WinEvent -FilterHashtable @{{LogName='Application'; ProviderName='Microsoft-Windows-User Profiles Service'; StartTime=$start; EndTime=$end}} -MaxEvents 200 |
    Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message
}} catch {{ $app = @([pscustomobject]@{{unavailable=$true; source='Application/User Profiles Service'; error=$_.Exception.Message}}) }}
$security = @()
try {{
  $security = Get-WinEvent -FilterHashtable @{{LogName='Security'; Id=4688; StartTime=$start; EndTime=$end}} -MaxEvents 200 |
    Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message
}} catch {{ $security = @([pscustomobject]@{{unavailable=$true; source='Security/4688'; error=$_.Exception.Message}}) }}
[pscustomobject]@{{applicationUserProfileEvents=$app; securityProcessCreationEvents=$security}} | ConvertTo-Json -Depth 6
"""
    return run_powershell_json(script, timeout=45)


def collect_windows_profile_state() -> dict[str, Any]:
    script = r"""
$ErrorActionPreference = 'Continue'
$profileList = @()
try {
  $profileList = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\*' |
    Select-Object PSChildName, ProfileImagePath, State, Flags, RefCount, Sid
} catch { $profileList = @([pscustomobject]@{unavailable=$true; source='ProfileList'; error=$_.Exception.Message}) }
$tempProfiles = @()
try {
  $tempProfiles = Get-ChildItem "$env:SystemDrive\Users" -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'TEMP*' -or $_.Name -eq 'Default' } |
    Select-Object FullName, Name, CreationTimeUtc, LastWriteTimeUtc, Attributes
} catch { $tempProfiles = @([pscustomobject]@{unavailable=$true; source='UsersRoot'; error=$_.Exception.Message}) }
$hives = @()
foreach ($path in @("$env:USERPROFILE\NTUSER.DAT", "$env:LOCALAPPDATA\Microsoft\Windows\UsrClass.dat")) {
  try {
    if (Test-Path -LiteralPath $path) {
      $item = Get-Item -LiteralPath $path -Force
      $acl = Get-Acl -LiteralPath $path
      $hives += [pscustomobject]@{
        path=$path; exists=$true; length=$item.Length; lastWriteTimeUtc=$item.LastWriteTimeUtc;
        owner=$acl.Owner; access=($acl.Access | Select-Object IdentityReference, FileSystemRights, AccessControlType, IsInherited)
      }
    } else {
      $hives += [pscustomobject]@{path=$path; exists=$false}
    }
  } catch { $hives += [pscustomobject]@{path=$path; unavailable=$true; error=$_.Exception.Message} }
}
$reparse = @()
try {
  foreach ($root in @("$env:USERPROFILE", "$env:SystemDrive\Users\Default")) {
    if (Test-Path -LiteralPath $root) {
      $reparse += Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue |
        Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
        Select-Object FullName, Name, LinkType, Target, Attributes
    }
  }
} catch { $reparse = @([pscustomobject]@{unavailable=$true; source='reparse'; error=$_.Exception.Message}) }
[pscustomobject]@{profileList=$profileList; defaultAndTempProfiles=$tempProfiles; profileHives=$hives; topLevelReparsePoints=$reparse} | ConvertTo-Json -Depth 8
"""
    return run_powershell_json(script, timeout=45)


def build_report(start: datetime | None, end: datetime | None, runtime_root: Path) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        "evidenceSources": [
            "V8 SQLite runtime stores under runtimeRoot, read-only mode",
            "Windows Application log / Microsoft-Windows-User Profiles Service",
            "Windows Security log / process creation 4688 when accessible",
            "ProfileList registry, profile hive ACL, Default/TEMP profile directories, top-level reparse points",
        ],
        "v8": collect_v8_sqlite_evidence(runtime_root, start, end),
        "windowsEvents": collect_windows_profile_events(start, end),
        "windowsProfileState": collect_windows_profile_state(),
    }


def build_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Windows Profile Incident Forensics",
        "",
        f"- generatedAt: `{report.get('generatedAt')}`",
        f"- window: `{json.dumps(report.get('window') or {}, ensure_ascii=False)}`",
        f"- runtimeRoot: `{((report.get('v8') or {}).get('runtimeRoot'))}`",
        "",
        "## Evidence Sources",
    ]
    for source in list(report.get("evidenceSources") or []):
        lines.append(f"- {source}")
    v8 = report.get("v8") or {}
    dbs = list(v8.get("databases") or [])
    lines.extend(["", "## V8 Evidence", f"- databases inspected: `{len(dbs)}`"])
    for db_entry in dbs:
        tables = list(db_entry.get("tables") or [])
        status = "unavailable" if db_entry.get("unavailable") else "ok"
        lines.append(f"- `{db_entry.get('path')}`: {status}, matchedTables={len(tables)}")
        for table in tables[:8]:
            lines.append(f"  - `{table.get('table')}` matchedRows={len(table.get('matchedRows') or [])}")
    lines.extend(["", "## Windows Events"])
    windows_events = report.get("windowsEvents") or {}
    if not windows_events.get("available", False):
        lines.append(f"- unavailable: `{windows_events.get('error') or windows_events.get('stderr') or windows_events}`")
    else:
        lines.append("- collected: available")
    lines.extend(["", "## Windows Profile State"])
    profile_state = report.get("windowsProfileState") or {}
    if not profile_state.get("available", False):
        lines.append(f"- unavailable: `{profile_state.get('error') or profile_state.get('stderr') or profile_state}`")
    else:
        lines.append("- collected: available")
    lines.extend(["", "## Notes", "- This script is read-only except for writing the requested report files. It does not repair or mutate system state."])
    return "\n".join(lines) + "\n"


def write_outputs(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    if output.suffix.lower() in {".md", ".json"}:
        output_dir = output.parent
        stem = output.with_suffix("").name
    else:
        output_dir = output
        stem = "windows_profile_incident_forensics"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(build_markdown_report(report), encoding="utf-8")
    return md_path, json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Windows profile incident evidence collector.")
    parser.add_argument("--start", help="Inclusive ISO timestamp. Example: 2026-05-01T00:00:00+08:00")
    parser.add_argument("--end", help="Inclusive ISO timestamp. Example: 2026-05-03T23:59:59+08:00")
    parser.add_argument("--runtime-root", default=str(Path.home() / ".v8-agent-os"))
    parser.add_argument("--output", help="Output directory or .md/.json base path. If omitted, markdown is printed.")
    args = parser.parse_args(argv)

    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    report = build_report(start, end, Path(args.runtime_root).expanduser())
    if args.output:
        md_path, json_path = write_outputs(report, Path(args.output).expanduser())
        print(f"wrote markdown: {md_path}")
        print(f"wrote json: {json_path}")
    else:
        sys.stdout.write(build_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
