"""Opt-in ACP stdio -> existing Admin -> real Supervisor audit.

Creates only a temporary workspace/new session. Authentication stays in the
bridge via the existing loopback local-session endpoint; no secrets in argv/env.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--admin-url", default="http://127.0.0.1:9528")
    parser.add_argument("--case", choices=["prompt", "ask", "cancel", "approval", "approval-external"], default="prompt")
    parser.add_argument("--approval-decision", choices=["approve", "reject"], default="approve")
    args = parser.parse_args()
    engine = Path(__file__).resolve().parents[2]
    workspace = Path(tempfile.mkdtemp(prefix="v8os-acp-live-"))
    approval_case = args.case.startswith("approval")
    rejection_case = approval_case and args.approval_decision == "reject"
    approval_root = Path(tempfile.mkdtemp(prefix="v8os-acp-approval-target-")) if args.case == "approval-external" else workspace
    approval_target = approval_root / "acceptance.txt" if approval_case else None
    if approval_target:
        approval_target.write_text("Disposable ACP approval fixture, created by this audit only.", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"V8OS_CLIENT_TOKEN", "V8OS_ADMIN_TOKEN"}}
    env["V8OS_ADMIN_URL"] = args.admin_url
    child = subprocess.Popen([shutil.which("node") or "node", str(engine.parent / "v8-agent-os-cli/bin/v8os.mjs"), "acp"],
                             cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8")
    incoming = queue.Queue()
    errors = []
    def read():
        for line in child.stdout:
            try:
                incoming.put(json.loads(line))
            except ValueError:
                incoming.put({"transport_error": "non_json_stdout"})
    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=lambda: errors.extend(child.stderr.readlines()), daemon=True).start()
    def send(method, params, request_id=None):
        packet = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            packet["id"] = request_id
        child.stdin.write(json.dumps(packet, ensure_ascii=False)+"\n")
        child.stdin.flush()
    def receive_response(request_id, deadline):
        messages = []
        while time.monotonic() < deadline:
            message = incoming.get(timeout=max(.1, deadline-time.monotonic()))
            messages.append(message)
            if message.get("id") == request_id and ("result" in message or "error" in message):
                return messages
        raise TimeoutError("ACP response did not arrive")
    report = {"live": True, "layer": "node_cli_stdio_admin_real_supervisor", "case": args.case, "passed": False}
    session_id = None
    try:
        send("initialize", {"protocolVersion": 1, "clientCapabilities": {"elicitation": {"form": {}}}}, 1)
        assert receive_response(1, time.monotonic()+15)[-1].get("result", {}).get("protocolVersion") == 1
        send("session/new", {"cwd": str(workspace), "mcpServers": []}, 2)
        created = receive_response(2, time.monotonic()+30)[-1]
        session_id = created.get("result", {}).get("sessionId")
        assert session_id, "session_new_failed"
        report["sessionId"] = session_id
        if approval_case:
            send("session/set_mode", {"sessionId": session_id, "modeId": "manual"}, 20)
            assert "result" in receive_response(20, time.monotonic()+10)[-1], "mode_failed"
        nonce = "ACP-LIVE-" + uuid.uuid4().hex[:12]
        prompt = (f"本次是链路验收。只回复 {nonce}，不要调用工具，也不要创建文件。" if args.case == "prompt" else
                  "本次是链路验收。请必须调用 ask_user 询问我的验收口令，等待答复后只复述口令，不要在普通text消息里问。" if args.case == "ask" else
                  f"本次测试命令安全审批。我已核实这个专用临时文件是本轮刚创建的可丢弃测试数据：{approval_target}。请用command工具在PowerShell执行 Remove-Item -LiteralPath '{approval_target}'，删除且仅删除这个文件，不使用-Recurse。遵守手动审批，不要用ask_user替代审批，不换其他工具绕过审批，也不要委派。确认文件已删除后只回复 {nonce}。" if approval_case else
                  "请仔细逐项分析一百种排序算法的复杂度并制作详细比较，不要使用工具或创建文件。")
        started = time.monotonic()
        send("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]}, 3)
        text, kinds, first_ms, interaction, cancelled, permission = "", {}, None, False, False, False
        first_text_ms = None
        deadline = started+240
        while time.monotonic() < deadline:
            message = incoming.get(timeout=max(.1, deadline-time.monotonic()))
            method = message.get("method")
            if method == "session/update":
                update = message["params"]["update"]
                kind = update.get("sessionUpdate")
                kinds[kind] = kinds.get(kind, 0)+1
                if first_ms is None:
                    first_ms = round((time.monotonic()-started)*1000)
                if kind == "agent_message_chunk":
                    if first_text_ms is None:
                        first_text_ms = round((time.monotonic()-started)*1000)
                    text += update["content"]["text"]
                if args.case == "cancel" and not cancelled:
                    send("session/cancel", {"sessionId": session_id})
                    cancelled = True
            elif method == "elicitation/create":
                interaction = True
                child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"action": "accept", "content": {"answer": nonce}}})+"\n")
                child.stdin.flush()
            elif method == "session/request_permission":
                permission = True
                tool = message.get("params", {}).get("toolCall") or {}
                # Approval fixture writes only a brand-new isolated file. Other
                # test cases never authorize unexpected actions.
                target = str((tool.get("rawInput") or {}).get("target") or tool.get("title") or "")
                allowed = approval_case and str(approval_target).replace("/", "\\").casefold() in target.replace("/", "\\").casefold()
                selected = "approve" if allowed and not rejection_case else "deny"
                report.setdefault("approvalDecisions", []).append({"exactFixtureTargetPresent": allowed, "fileExistedBeforeApproval": approval_target.exists() if approval_target else None, "selectedOption": selected})
                child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "selected", "optionId": selected}}})+"\n")
                child.stdin.flush()
            if message.get("id") == 3:
                report.update({"elapsedMs": round((time.monotonic()-started)*1000), "firstUpdateMs": first_ms,
                               "firstTextMs": first_text_ms,
                               "eventCounts": kinds, "answerChars": len(text), "answerSha256": hashlib.sha256(text.encode()).hexdigest(),
                               "noncePresent": nonce in text, "elicitationObserved": interaction,
                               "permissionObserved": permission,
                               "result": message.get("result"), "error": message.get("error")})
                assert "error" not in message, "prompt_failed"
                assert message["result"]["stopReason"] == ("cancelled" if args.case == "cancel" else "refusal" if rejection_case else "end_turn"), "wrong_stop_reason"
                assert args.case == "cancel" or rejection_case or nonce in text, "answer_missing"
                assert args.case == "cancel" or rejection_case or text.count(nonce) == 1, "answer_duplicated"
                assert args.case != "ask" or interaction, "ask_user_not_forwarded"
                if approval_case:
                    assert permission, "approval_not_requested"
                    if rejection_case:
                        assert approval_target.exists(), "rejected_operation_still_executed"
                        report["rejectedOperationDidNotExecute"] = True
                    else:
                        assert not approval_target.exists(), "approved_fixture_delete_not_completed"
                        report["fixtureSideEffectVerified"] = True
                break
        else:
            raise TimeoutError("prompt_timeout")
        send("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []}, 4)
        replay = receive_response(4, time.monotonic()+30)
        replay_text = "".join(m["params"]["update"]["content"]["text"] for m in replay if m.get("method") == "session/update" and m["params"]["update"]["sessionUpdate"] == "agent_message_chunk")
        answer_expected = args.case != "cancel" and not rejection_case
        report["historyAnswerPresent"] = nonce in replay_text if answer_expected else None
        if answer_expected:
            assert report["historyAnswerPresent"], "history_replay_missing_answer"
        # Read only identity/terminal fields from the canonical authenticated
        # session. Do not retain provider configuration or response bodies.
        import sys
        if str(engine) not in sys.path:
            sys.path.insert(0, str(engine))
        from acp_bridge.backend import AdminBffBackend
        detail = AdminBffBackend(admin_url=args.admin_url).load_session(session_id=session_id).raw
        run = detail.get("currentRun") or {}
        metadata = run.get("metadata") or {}
        report["provider"] = metadata.get("provider")
        report["model"] = metadata.get("model")
        report["persistedRunStatus"] = run.get("status")
        report["persistedSafetyMode"] = metadata.get("safetyApprovalMode")
        if args.case == "cancel":
            assert run.get("status") in {"cancelled", "canceled"}, "history_cancel_not_confirmed"
        if approval_case:
            assert metadata.get("safetyApprovalMode") == "manual", "manual_mode_not_persisted"
            if not rejection_case:
                assert run.get("status") == "completed", "engine_did_not_complete_approved_run"
        report["passed"] = True
    except Exception as exc:
        report["failure"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        if session_id:
            send("session/cancel", {"sessionId": session_id})
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=12)
        except subprocess.TimeoutExpired:
            child.kill()
        report["exitCode"] = child.returncode
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
