"""Deterministic, public synthetic inputs. Nothing comes from a user database."""
import argparse
import json
from pathlib import Path


def build():
    return {
        "fixtureVersion": "experience-9131-v1",
        "qualification": "Scenario data, not claims that these are current API schemas. Adapters must map the canonical production contract explicitly.",
        "profiles": [{"profileId": f"profile-{key}", "authorityInstanceId": f"authority-{key}", "servingInstanceId": f"instance-{key}", "principalId": "OwnerCaseSensitive", "sessionId": "session-1", "messageId": "message-1", "text": f"ONLY-{key}", "cursor": f"cursor-{key}", "tombstone": key == "A", "resourceId": "resource-1", "resourceContent": f"resource-{key}"} for key in ["A", "B"]],
        "endpointAlias": {"profileId": "profile-A", "authorityInstanceId": "authority-A", "before": "https://lan.fixture.invalid", "after": "https://tunnel.fixture.invalid"},
        "drafts": [{"sessionId": f"session-{key}", "revision": 2, "text": f"合成草稿 {key}：中文🙂\n第二行", "selection": {"start": 2, "end": 6}, "attachments": [{"id": f"attachment-{key}", "uploadState": "ready"}], "plugins": [f"plugin-{key}"], "skills": [f"skill-{key}"], "scrollAnchor": {"messageId": "message-1", "offset": 24}} for key in ["A", "B"]],
        "queue": {"session-A": [{"id": "queued-A", "clientMessageId": "submit-A-v1", "status": "pending", "text": "合成排队输入"}], "session-B": []},
        "config": {"revision": "rev-A-1", "data": {"name": "合成对象", "unknownFuture": {"enabled": False, "zero": 0, "empty": "", "nested": [1, {"opaque": "keep-me"}]}, "secret": "__UNCHANGED_SECRET_PLACEHOLDER__", "credentialRef": "synthetic-ref-only", "toolAllowlist": [], "prompt": "第一行\n```literal```\n末行🙂"}},
        "longForm": [{"fieldId": f"synthetic-field-{i:02}", "value": i} for i in range(50)],
        "catalog": [{"provider": provider, "skillId": "same-id", "name": "同名合成技能", "revision": f"{provider}-rev-1", "files": ["SKILL.md", "scripts/run.py", "references/guide.md", "assets/checker.png"]} for provider in ["international", "modelscope"]],
        "longReadme": "\n\n".join(f"## 合成段落 {i}\n只用于长文与固定 footer 验收。" for i in range(100)),
        "graph": {"global": ["entity-shared", "global-only"], "workspace-A": ["entity-shared", "A-only"], "workspace-B": ["entity-shared", "B-only"], "denied-workspace": ["must-not-be-in-summary"]},
        "faultSchedule": [{"request": "A", "startMs": 0, "resolveMs": 2000}, {"request": "B", "startMs": 100, "resolveMs": 200}, {"request": "A-submit-v1", "startMs": 0, "resolveMs": 2000}, {"event": "input-v2", "atMs": 150}],
        "faults": ["503", "403", "timeout-after-commit", "409 revision conflict", "abort-ignored-late-response", "duplicate-ack", "snapshot-omission", "explicit-empty-queue", "SecureStore-reject", "SQLite-write-fail", "WS1006", "media-play-reject", "all-media-404"],
        "scales": {"sessions": [100, 1000, 10000], "messages": [50, 500, 5000], "profiles": [1, 5, 20, 100], "peersSeparateFromProfiles": [1, 5, 20, 100], "streamEventsPerSecond": [10, 100, 500], "warmSamples": 30, "navigationCycles": 20, "terminalBytes": 52428800},
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Generated synthetic experience-9131-v1 fixture")
