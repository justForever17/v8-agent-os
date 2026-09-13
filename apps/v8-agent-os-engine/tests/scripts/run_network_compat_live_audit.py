"""Opt-in real-provider compat HTTP audit; source credentials stay in process memory."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch


def audit(root: Path, models: dict, port: int, *, protocol: str = "openai", approval_only: bool = False, tool_only: bool = False) -> dict:
    import httpx
    import uvicorn
    from core.storage import storage
    from core.tools.native.mcp import config_broker
    from erc.runtime_context import bind_runtime_context

    with patch.object(storage, "get_models_config", side_effect=lambda: deepcopy(models)):
        from core.model_control_plane import model_control_plane
        resolution = model_control_plane.resolve_model_for_role("supervisor")
        report = {"live": True, "layer": "real_provider_http", "protocol": protocol, "port": port,
                  "modelId": resolution.get("resolvedModelId"), "providerId": resolution.get("resolvedProviderId"), "cells": []}
        with bind_runtime_context(user_id="compat-audit", agent_id="supervisor", actor_role="supervisor", safety_approval_mode="minimal"):
            plan = json.loads(config_broker.invoke({"mode": "network_prepare", "network_settings": {
                "enabled": True, "discovery": {"lanEnabled": False}, "relay": {"enabled": False},
                "openaiCompat": {"enabled": True, "modelAliases": ["v8os"], "requestTimeoutSeconds": 120},
            }}))
            assert plan["ok"], plan
            receipt = json.loads(config_broker.invoke({"mode": "commit", "transaction_id": plan["transactionId"], "plan_digest": plan["planDigest"]}))
            assert receipt["ok"], receipt
        from runtimes.network_supervisor.service import network_supervisor_service
        from core.model_failover_service import model_failover_service
        from core.system_base import get_internal_secret
        import main
        from runtimes.chat.runtime import chat_runtime
        original_failed = chat_runtime.finalize_failed_run
        runtime_failures = []
        def observed_failure(chat_run, exc, stream_state=None):
            import traceback
            runtime_failures.append({"type": type(exc).__name__, "code": getattr(exc, "failure_class", None),
                "frames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name} for f in traceback.extract_tb(exc.__traceback__)[-7:]]})
            return original_failed(chat_run, exc, stream_state)
        failure_observation = patch.object(chat_runtime, "finalize_failed_run", side_effect=observed_failure)
        failure_observation.start()
        actual_invocations = []
        original_invoke = model_failover_service.invoke_with_failover
        def observed_invoke(*args, **kwargs):
            row = {"role": kwargs.get("role"), "toolChoice": kwargs.get("tool_choice"), "toolCount": len(kwargs.get("tools") or [])}
            actual_invocations.append(row)
            value = original_invoke(*args, **kwargs)
            row["returnedToolCalls"] = len(getattr(value, "tool_calls", []) or [])
            return value
        observation_patch = patch.object(model_failover_service, "invoke_with_failover", side_effect=observed_invoke)
        observation_patch.start()
        token = network_supervisor_service.create_openai_compat_token("isolated compat audit")["token"]
        other = network_supervisor_service.create_openai_compat_token("isolated wrong identity")["token"]
        headers = {"Authorization": "Bearer " + token, "X-V8-Agent-OS-Secret": get_internal_secret(), "X-V8-External-Thread-Id": "compat-audit-thread"}
        server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 90
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("isolated_engine_not_ready")
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}/v1/network-supervisor/{protocol}", headers=headers, timeout=140)

        def request(case: str, body: dict, *, expected: int = 200, extra_headers: dict | None = None):
            started = time.monotonic()
            response = client.post("/messages" if protocol == "anthropic" else "/chat/completions", json=body, headers=extra_headers)
            data = response.json()
            report["cells"].append({"case": case, "elapsedMs": round((time.monotonic()-started)*1000),
                                    "httpStatus": response.status_code, "expectedHttpStatus": expected,
                                    "responseSha256": hashlib.sha256(response.content).hexdigest(), "passed": response.status_code == expected,
                                    "run": data.get("v8os_run", {}),
                                    "error": data.get("error", {}).get("code") if isinstance(data.get("error"), dict) else None})
            # Only the generated fixture dialogue is retained; never source config/headers.
            if isinstance(report["cells"][-1]["run"], dict):
                report["cells"][-1]["run"].pop("content", None)
            assert response.status_code == expected, f"{case}: HTTP {response.status_code}"
            return data

        try:
            if approval_only:
                fixture_target = root / "fixture" / "NTUSER.DAT"
                fixture_target.parent.mkdir()
                fixture_target.write_text("Synthetic fixture, not a Windows user hive. FINAL_CODE=APPROVED-ROUNDTRIP-4836", encoding="utf-8")
                tool = {"type": "function", "function": {"name": "Read", "description": "Read a local file, only after V8 Safety approval.",
                        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}}
                prompt = {"role": "user", "content": "请用 Read 读取测试目录里的普通文本文件 " + str(fixture_target) + "。这是我们创建的无隐私fixture，不是真实Windows文件；工具仍须经过V8审批。不要调用其他工具或绕过审批。收到工具返回后，只回复其中FINAL_CODE的值。"}
                first = request("approval_request", {"model": "v8os", "messages": [prompt], "tools": [tool], "tool_choice": {"type": "function", "function": {"name": "Read"}}})
                handle = first.get("v8os_run") or {}
                assert handle.get("status") == "waiting_approval" and handle.get("approvals"), "approval_not_requested"
                assert not first["choices"][0]["message"].get("tool_calls"), "released_before_approval"
                approval_id = handle["approvals"][0]["approvalId"]
                t = time.monotonic()
                # This is the existing governed local Admin/Owner API, never the
                # compat API key granting itself approval. No protected I/O occurs.
                approved = client.post(f"http://127.0.0.1:{port}/v1/approvals/{approval_id}/approve",
                    headers={"X-V8-Agent-OS-User-Email": "compat-audit-admin", "X-V8-Admin-Role": "ADMIN"},
                    json={"response": {"approved": True, "decision": "approved"}})
                report["cells"].append({"case": "native_owner_approval", "passed": approved.status_code == 200, "httpStatus": approved.status_code, "approvalId": approval_id,
                                        "elapsedMs": round((time.monotonic()-t)*1000), "layer": "real_local_admin_api_no_ui"})
                assert approved.status_code == 200, "native_approval_failed"
                control = {"v8os_control": {"action": "status", "runId": handle["runId"]}}
                deadline = time.monotonic()+90
                resumed = {}
                while time.monotonic() < deadline:
                    resumed = client.post("/chat/completions", json=control).json().get("v8os_run") or {}
                    if resumed.get("tool_calls") or resumed.get("status") in {"failed", "completed", "cancelled"}:
                        break
                    time.sleep(0.5)
                report["cells"].append({"case": "approved_original_run_external_handoff", "status": resumed.get("status"), "deliveryError": resumed.get("deliveryError"),
                                        "passed": resumed.get("runId") == handle["runId"] and bool(resumed.get("tool_calls")) and resumed.get("status") == "waiting_external_tool"})
                assert report["cells"][-1]["passed"], "approved_tool_contract_not_restored"
                calls = resumed["tool_calls"]
                reply = {"model": "v8os", "tools": [tool], "messages": [prompt, {"role": "assistant", "content": None, "tool_calls": calls},
                    {"role": "tool", "tool_call_id": calls[0]["id"], "name": "Read", "content": "Synthetic external-client test result only. No real protected file was read. FINAL_CODE=APPROVED-ROUNDTRIP-4836"}]}
                final = request("approved_tool_fixture_result", reply)
                assert "APPROVED-ROUNDTRIP-4836" in str(final.get("choices")), "approved_fixture_not_delivered"
                report["cells"].append({"case": "same_run_approval_closure", "passed": final.get("v8os_run", {}).get("runId") == handle["runId"] and final.get("v8os_run", {}).get("status") == "completed",
                    "approvalId": approval_id, "protectedFileRead": False})
                assert report["cells"][-1]["passed"], "approval_run_identity_drift"
                report["passed"] = True
                return report
            if protocol == "anthropic":
                started = time.monotonic()
                response = client.post("/messages", json={"model": "v8os", "max_tokens": 128, "stream": True,
                    "messages": [{"role": "user", "content": "只回复 ANTHROPIC-STREAM-OK，不调用工具。"}]})
                data = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                deltas = "".join(str(item.get("delta", {}).get("text") or "") for item in data)
                report["cells"].append({"case": "anthropic_stream", "passed": response.status_code == 200 and "ANTHROPIC-STREAM-OK" in deltas and any(item.get("type") == "message_stop" for item in data), "elapsedMs": round((time.monotonic()-started)*1000)})
                assert report["cells"][-1]["passed"], "anthropic_stream_mapping_failed"
                tool = {"name": "read_fixture_code", "description": "Read the nonce at the end of a synthetic audit record. No side effects.", "input_schema": {"type": "object", "properties": {}}}
                prompt = {"role": "user", "content": "调用 read_fixture_code，结果只回答 FINAL_CODE 的值。"}
                first = request("anthropic_tool_use", {"model": "v8os", "max_tokens": 256, "messages": [prompt], "tools": [tool], "tool_choice": {"type": "tool", "name": "read_fixture_code"}})
                use = next(block for block in first["content"] if block.get("type") == "tool_use")
                continuation = {"model": "v8os", "max_tokens": 256, "tools": [tool], "messages": [prompt,
                    {"role": "assistant", "content": first["content"]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use["id"], "content": "record; "*1000+"\nFINAL_CODE=ANTHROPIC-TAIL-6248"}]}]}
                request("anthropic_foreign_key", continuation, expected=409, extra_headers={"Authorization": "Bearer "+other})
                final = request("anthropic_tool_result", continuation)
                assert "ANTHROPIC-TAIL-6248" in json.dumps(final.get("content")), "anthropic_tail_lost"
                request("anthropic_duplicate_result", continuation, expected=409)
                paused = request("anthropic_ask_user", {"model": "v8os", "max_tokens": 256, "messages": [{"role": "user", "content": "必须用ask_user问我验收口令并等待，收到后只复述口令。不准在普通文本提问。"}]})
                handle = paused.get("v8os_run") or {}
                assert handle.get("status") == "waiting_input" and handle.get("questions"), "anthropic_wait_missing"
                reply = {"v8os_control": {"action": "answer", "runId": handle["runId"], "interactionId": handle["questions"][0]["interactionId"], "answer": "ANTHROPIC-RESUME-8724"}}
                request("anthropic_answer", reply)
                deadline = time.monotonic()+90
                while time.monotonic() < deadline:
                    final = client.post("/messages", json={"v8os_control": {"runId": handle["runId"], "action": "status"}}).json().get("v8os_run") or {}
                    if final.get("status") in {"completed", "failed", "cancelled"}:
                        break
                    time.sleep(0.5)
                report["cells"].append({"case": "anthropic_resumed_delivery", "passed": final.get("status") == "completed" and "ANTHROPIC-RESUME-8724" in str(final.get("content"))})
                assert report["cells"][-1]["passed"], "anthropic_resume_failed"
                report["passed"] = True
                return report
            request("invalid_token", {"model": "v8os", "messages": [{"role": "user", "content": "hello"}]}, expected=401, extra_headers={"Authorization": "Bearer invalid-fixture"})
            if not tool_only:
                first = request("stateless_first", {"model": "v8os", "messages": [{"role": "user", "content": "只回复兼容测试通过，不要调用工具。"}]})
                assert "兼容测试通过" in str(first.get("choices")), "real_provider_answer_missing"
            tool = {"type": "function", "function": {"name": "read_fixture_code", "description": "Read the nonce at the end of a synthetic audit record. Read-only, no side effect.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
            prompt = {"role": "user", "content": "请调用 read_fixture_code，得到结果后只回复末尾 FINAL_CODE 的值，不要调用其他工具。"}
            start = request("tool_request", {"model": "v8os", "messages": [prompt], "tools": [tool], "tool_choice": {"type": "function", "function": {"name": "read_fixture_code"}}})
            assistant = start["choices"][0]["message"]
            call = assistant["tool_calls"][0]
            assert call["function"]["name"] == "read_fixture_code"
            result = {"role": "tool", "tool_call_id": call["id"], "name": "read_fixture_code", "content": ("synthetic row; " * 500) + "\nFINAL_CODE=V8-COMPAT-TAIL-9274"}
            continuation = {"model": "v8os", "messages": [prompt, assistant, result], "tools": [tool]}
            request("foreign_key", continuation, expected=409, extra_headers={"Authorization": "Bearer " + other})
            request("foreign_thread", continuation, expected=409, extra_headers={"X-V8-External-Thread-Id": "wrong-thread"})
            if tool_only:
                request("bad_alias_preserves_result", {**continuation, "model": "invalid-fixture"}, expected=404)
                malformed = deepcopy(continuation)
                malformed["tools"][0]["function"]["parameters"]["properties"] = []
                request("bad_schema_preserves_result", malformed, expected=400)
            answer = request("tool_result_long_tail", continuation)
            assert "V8-COMPAT-TAIL-9274" in str(answer.get("choices")), "tool_result_tail_lost"
            request("duplicate_result", continuation, expected=409)
            if tool_only:
                receipt = next(item for item in network_supervisor_service.pending_external_tools_snapshot().values() if item.get("wireToolCallId") == call["id"])
                status = network_supervisor_service.external_tool_receipt_status(receipt)
                stored_result = network_supervisor_service._pending_store().receipt(status["receiptId"])
                report["cells"].append({"case": "durable_full_result_receipt", "passed": stored_result["content"] == result["content"]
                    and status["deliveryState"] == "completed" and status["deliveryRunId"] == answer["v8os_run"]["runId"],
                    "receiptId": status["receiptId"], "deliveryRunId": status["deliveryRunId"], "deliveryState": status["deliveryState"],
                    "contentSha256": hashlib.sha256(stored_result["content"].encode()).hexdigest()})
                assert report["cells"][-1]["passed"], "durable_receipt_not_linked_to_delivery"
                report["passed"] = True
                return report
            waiting = request("ask_user", {"model": "v8os", "messages": [{"role": "user", "content": "请必须调用 ask_user 工具询问我的验收口令，等待我的答复后再只复述我给的口令。不要在普通文本里提问，不要猜测口令。"}]})
            handle = waiting.get("v8os_run") or {}
            assert handle.get("status") == "waiting_input" and handle.get("questions"), "ask_user_wait_not_visible"
            control = {"runId": handle["runId"], "action": "status"}
            request("foreign_run", {"v8os_control": control}, expected=404, extra_headers={"Authorization": "Bearer " + other})
            question = handle["questions"][0]["interactionId"]
            reply = {"v8os_control": {"runId": handle["runId"], "action": "answer", "interactionId": question, "answer": "V8-ANSWER-6139"}}
            request("answer_resume", reply)
            request("duplicate_answer", reply, expected=409)
            deadline = time.monotonic() + 90
            final = {}
            while time.monotonic() < deadline:
                final = client.post("/chat/completions", json={"v8os_control": control}).json().get("v8os_run") or {}
                if final.get("status") in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.5)
            report["cells"].append({"case": "ask_user_resumed_delivery", "passed": final.get("status") == "completed" and "V8-ANSWER-6139" in str(final.get("content")), "status": final.get("status")})
            assert report["cells"][-1]["passed"], "ask_user_resume_not_delivered"
            # A real model requests a protected read; the external client does
            # not execute it. Safety must interrupt before a runnable wire call.
            guarded_tool = {"type": "function", "function": {"name": "Read", "description": "Read a local file. V8 Safety approval is required for protected targets.",
                            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}}
            guarded_prompt = {"role": "user", "content": "请用 Read 读取 " + str(Path.home()/"NTUSER.DAT") + "；这是审批流程验收，只请求审批，不使用其他工具，不绕过审批。"}
            guarded = request("approval_wait", {"model": "v8os", "messages": [guarded_prompt], "tools": [guarded_tool],
                                             "tool_choice": {"type": "function", "function": {"name": "Read"}}})
            approval_handle = guarded.get("v8os_run") or {}
            no_call = not any(choice.get("message", {}).get("tool_calls") for choice in guarded.get("choices", []))
            report["cells"].append({"case": "approval_does_not_release_external_action", "passed": no_call and approval_handle.get("status") == "waiting_approval" and bool(approval_handle.get("approvals"))})
            assert report["cells"][-1]["passed"], "external_action_exposed_before_approval"
            request("api_cannot_self_approve", {"v8os_control": {"runId": approval_handle["runId"], "action": "approve"}}, expected=400)
            cancelled = request("cancel_approval_run", {"v8os_control": {"runId": approval_handle["runId"], "action": "cancel"}})
            assert cancelled["v8os_run"]["status"] in {"cancelled", "cancelling"}, "cancel_not_acknowledged"
            report["cancelledProtectedReadExecuted"] = False
            # Streaming must keep exactly the same wait truth; no tool delta may
            # be emitted speculatively before the approval check finishes.
            t = time.monotonic()
            streamed = client.post("/chat/completions", json={"model": "v8os", "messages": [guarded_prompt], "tools": [guarded_tool],
                "stream": True, "tool_choice": {"type": "function", "function": {"name": "Read"}}})
            frames = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith("data: ") and line[6:] != "[DONE]"]
            stream_handle = next((f["v8os_run"] for f in frames if "v8os_run" in f), {})
            leaked = any(c.get("delta", {}).get("tool_calls") for f in frames for c in f.get("choices", []))
            report["cells"].append({"case": "stream_approval_wait", "elapsedMs": round((time.monotonic()-t)*1000), "passed": streamed.status_code == 200 and stream_handle.get("status") == "waiting_approval" and not leaked})
            assert report["cells"][-1]["passed"], "stream_external_action_exposed_before_approval"
            request("cancel_stream_approval", {"v8os_control": {"runId": stream_handle["runId"], "action": "cancel"}})
        except Exception as exc:
            report["failureType"] = type(exc).__name__
            report["failureCode"] = str(exc) if isinstance(exc, AssertionError) else "live_boundary_failed"
        finally:
            client.close()
            server.should_exit = True
            thread.join(timeout=15)
            report["engineStopped"] = not thread.is_alive()
            observation_patch.stop()
            failure_observation.stop()
            report["actualModelInvocations"] = actual_invocations
            report["runtimeFailures"] = runtime_failures
            if report.get("passed"):
                report["passed"] = report["engineStopped"]
        report["passed"] = not report.get("failureType") and all(item["passed"] for item in report["cells"]) and report["engineStopped"]
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--isolated-root", required=True)
    parser.add_argument("--port", type=int, default=19531)
    parser.add_argument("--protocol", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--approval-only", action="store_true", help="Only native approval -> original external call -> harmless fixture result; never reads the protected target.")
    parser.add_argument("--tool-only", action="store_true", help="OpenAI tool roundtrip plus invalid retries and durable full-result receipt; two real model calls.")
    args = parser.parse_args(argv)
    if args.tool_only and (args.approval_only or args.protocol != "openai"):
        parser.error("--tool-only is an independent OpenAI-only slice")
    if not args.live:
        print("Refused: --live required before config reads or provider calls.")
        return 2
    root = Path(args.isolated_root).resolve()
    if root.exists():
        parser.error("isolated root must not exist")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    source = Path(os.environ.get("V8_AGENT_OS_HOME") or Path.home()/".v8-agent-os")/"config.json"
    models = json.loads(source.read_text(encoding="utf-8"))["models"]
    root.mkdir(parents=True)
    os.environ["V8_AGENT_OS_HOME"] = str(root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            report = audit(root, models, args.port, protocol=args.protocol, approval_only=args.approval_only, tool_only=args.tool_only)
        except Exception as exc:
            import traceback
            report = {"passed": False, "errorType": type(exc).__name__, "errorFrames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name} for f in traceback.extract_tb(exc.__traceback__)[-5:]]}
    (root/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(root/"report.json"), "cases": [{"case": c["case"], "passed": c["passed"], "elapsedMs": c.get("elapsedMs")} for c in report.get("cells", [])], "errorType": report.get("errorType"), "errorFrames": report.get("errorFrames")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
