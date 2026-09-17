"""Measure real isolated Engine submission/events/history using governed config.

Outputs only synthetic IDs, topics, timing, lengths and hashes, never raw model
requests, credentials or provider responses. Requires run_first_chat_upgrade_live.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live: parser.error("--live is required")
    state = args.state.resolve()
    if state == (Path.home()/".v8-agent-os").resolve(): parser.error("isolated state required")
    config = json.loads((state/"config.json").read_text(encoding="utf-8-sig"))
    bridge = config["systemBase"]["bridge"]
    base = bridge["engineBaseUrl"].rstrip("/")
    http = requests.Session()
    http.headers["x-v8-agent-os-secret"] = bridge["internalSecret"]
    results = []
    def call(path, body=None):
        response = http.get(base+path, timeout=20) if body is None else http.post(base+path, json=body, timeout=90)
        if not response.ok:
            raise RuntimeError(f"{path.split('?')[0]} HTTP {response.status_code}")
        return response.json()
    for sample in range(args.samples):
        start = time.perf_counter()
        workspace = state/"workspace"/f"first-chat-{sample}"
        workspace.mkdir(parents=True, exist_ok=True)
        session = call("/sessions", {"title": f"首聊合成样本 {sample}", "userId": "fixture-upgrade-owner",
                                     "workspacePath": str(workspace), "scopeHint": "workspace", "scopeMode": "explicit"})
        sid = session.get("sessionId") or session["id"]
        row = {"sample": sample, "sessionId": sid, "sessionMs": round((time.perf_counter()-start)*1000, 1), "topics": []}
        submitted = time.perf_counter()
        message_id = "fixture-"+uuid.uuid4().hex
        accepted = call("/chat/submit", {"session_id": sid, "clientMessageId": message_id,
                        "messages": [{"id": message_id, "role": "user", "content": "你好"}],
                        "data": {"userId": "fixture-upgrade-owner", "conversationId": sid},
                        "workspace_path": str(workspace), "scope_hint": "workspace", "scope_mode": "explicit"})
        row["acceptedMs"] = round((time.perf_counter()-submitted)*1000, 1)
        row["accepted"] = accepted.get("accepted")
        row["runId"] = accepted.get("runId") or accepted.get("run_id")
        cursor = 0
        deadline = time.perf_counter()+120
        terminal = False
        while time.perf_counter() < deadline:
            payload = call(f"/sessions/{sid}/runtime-events?after_seq={cursor}&limit=200")
            events = payload.get("events", [])
            for event in events:
                cursor = max(cursor, int(event.get("seq") or 0))
                topic = event.get("topic") or event.get("event_type") or event.get("type")
                data = event.get("payload") or event.get("data") or {}
                now = round((time.perf_counter()-submitted)*1000, 1)
                entry = {"seq": cursor, "topic": topic, "observedMs": now, "at": event.get("created_at") or event.get("timestamp")}
                if topic == "run.text.delta":
                    text = str(data.get("content") or data.get("delta") or data.get("text") or "")
                    if text.strip(): row.setdefault("firstTextMs", now)
                    entry["chars"] = len(text)
                if isinstance(data.get("diagnostics"), dict): entry["diagnostics"] = {key: value for key,value in data["diagnostics"].items() if isinstance(value,(int,float,bool))}
                row["topics"].append(entry)
                if topic in {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}:
                    row["terminal"] = topic
                    row["completedMs"] = now
                    terminal = True
            if terminal: break
            time.sleep(0.2)
        history = call(f"/sessions/{sid}/history")
        row["messages"] = [{"id": m.get("id"), "role": m.get("role"), "chars": len(str(m.get("content") or "")),
                             "hash": hashlib.sha256(str(m.get("content") or "").encode()).hexdigest()}
                            for m in history.get("messages", history.get("timeline", []))]
        results.append(row)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({key:row.get(key) for key in ["sample","sessionMs","acceptedMs","firstTextMs","completedMs","terminal","messages"]},ensure_ascii=False),flush=True)
        if not terminal: break


if __name__ == "__main__": main()
