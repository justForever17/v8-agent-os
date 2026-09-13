"""Execute baseline Phone SQL in memory. Tests schema identity, not the native service."""
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--repo", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
args = p.parse_args()
source = args.repo / "apps/v8-agent-os-phone/src/services/LocalDatabaseService.ts"
text = source.read_text(encoding="utf-8")
schema = re.search(r"execAsync\(`(\s*CREATE TABLE[\s\S]+?)`\)", text)
message_sql = re.search(r"'(INSERT OR REPLACE INTO local_messages [^']+)'", text)
cursor_sql = re.search(r"'(INSERT OR REPLACE INTO local_sync_cursors [^']+)'", text)
if not all([schema, message_sql, cursor_sql]):
    raise RuntimeError("Baseline SQL adapter needs review; extraction failure is not a product test result")
db = sqlite3.connect(":memory:")
db.executescript(schema[1])
for authority in ["A", "B"]:
    db.execute(message_sql[1], ["message-1", "session-1", 1, "2026-01-01T00:00:00Z", None, None, json.dumps({"id": "message-1", "text": f"ONLY-{authority}"})])
    db.execute(cursor_sql[1], ["session-1", f"cursor-{authority}"])
rows = db.execute("SELECT raw_json FROM local_messages").fetchall()
cursors = db.execute("SELECT sync_cursor FROM local_sync_cursors").fetchall()
evidence = [{"id": "P05-SQL-identical-message-IDs", "expected": ["ONLY-A", "ONLY-B"], "actual": [json.loads(row[0])["text"] for row in rows], "status": "PASS" if len(rows) == 2 else "FAIL"}, {"id": "P05-SQL-independent-cursors", "expected": ["cursor-A", "cursor-B"], "actual": [row[0] for row in cursors], "status": "PASS" if len(cursors) == 2 else "FAIL"}]
report = {"sourceHead": subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip(), "source": source.relative_to(args.repo).as_posix(), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "level": "BOUNDARY_EXECUTED", "qualification": "Actual production CREATE/INSERT SQL with synthetic bound arguments and Python in-memory SQLite. No native service, real device or existing database accessed.", "evidence": evidence}
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(evidence))
raise SystemExit(0 if all(x["status"] == "PASS" for x in evidence) else 1)
