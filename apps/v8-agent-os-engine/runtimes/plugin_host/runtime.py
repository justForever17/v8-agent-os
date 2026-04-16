from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import traceback
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from agents.runners.supervisor_runner import supervisor_runner
from api.models import EngineConfig
from core.artifact_store import artifact_store
from core.chat_output_extractor import extract_text_and_reasoning
from core.database import db
from core.engine_config_resolver import resolve_engine_config_for_role
from core.graph_stream_watchdog import GraphStreamWatchdogState, next_graph_stream_event
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.plugin_host import plugin_host_service
from core.plugin_host.media_assets import build_tts_output_paths
from core.plugin_host.silk_codec import (
    SilkCodecError,
    encode_audio_to_silk,
    probe_audio_duration_ms,
    silk_toolchain_status,
    validate_and_normalize_tencent_silk,
)
from core.plugin_host.voice_profiles import (
    voice_profile_allows_audio_delivery,
    resolve_voice_delivery_profile,
    resolve_voice_encode_preset,
    voice_profile_requires_external_encoder,
)
from core.runtime_signal_ingress import build_normalized_signal_payload
from core.runtime_contexts import (
    build_plugin_host_context_messages,
    build_plugin_host_runtime_metadata,
    build_plugin_host_context_blocks,
    coerce_json_dict,
    select_channel_context_window,
)
from core.runtime.projection import build_chat_projection_snapshot
from core.context.workspace import workspace_resolution_service
from core.plugin_host.safety import assess_channel_inbound_group_risk
from erc.event_bus import event_bus
from erc.kernel import erc_kernel
from erc.models import RuntimeSource
from erc.runtime_control import apply_control_signal, consume_stop_signal
from erc.runtime_context import bind_runtime_context
from erc.runtime_registry import runtime_registry
from erc.runtime_stability import runtime_stability_service
from erc.session_admission_service import session_admission_service
from erc.safety_guardian import safety_guardian
from erc.snapshot_service import snapshot_service
from runtimes.memory.scope_resolution import scope_resolution_service, session_scope_binding_service


@dataclass(slots=True)
class PluginHostMessage:
    role: str
    content: str
    sender_name: str | None = None
    sender_id: str | None = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata or {})


def _extract_interrupt_request(chunk: dict | None) -> dict | None:
    if not isinstance(chunk, dict):
        return None
    raw_interrupts = chunk.get("__interrupt__")
    if not raw_interrupts:
        return None

    first_interrupt = raw_interrupts[0] if isinstance(raw_interrupts, (list, tuple)) else raw_interrupts
    payload = getattr(first_interrupt, "value", first_interrupt)
    interrupt_id = getattr(first_interrupt, "id", None)
    if not isinstance(payload, dict):
        payload = {"question": str(payload)}

    question = payload.get("question") or payload.get("prompt") or "当前渠道任务需要您的确认才能继续。"
    tool_call_id = payload.get("toolCallId") or payload.get("tool_call_id")
    approval_kind = payload.get("approvalKind") or payload.get("approval_kind") or "human_input_required"
    request_payload = dict(payload)
    request_payload["question"] = question
    request_payload["prompt"] = question
    request_payload["approvalKind"] = approval_kind
    if tool_call_id:
        request_payload["toolCallId"] = tool_call_id
    if interrupt_id:
        request_payload["interruptId"] = interrupt_id
    return request_payload


class PluginHostRuntime:
    kind = "plugin_host"

    _VOICE_BLOCK_RE = re.compile(r"<voice[^>]*>(.*?)</voice>", re.IGNORECASE | re.DOTALL)
    _VOICE_SECTION_RE = re.compile(r"<voice[^>]*>.*?</voice>", re.IGNORECASE | re.DOTALL)

    def _extract_voice_text(self, final_response: str) -> str:
        raw = str(final_response or "")
        voice_blocks = self._VOICE_BLOCK_RE.findall(raw)
        flattened = "\n".join(str(block).strip() for block in voice_blocks if str(block).strip()).strip()
        if flattened:
            return flattened
        if re.search(r"<voice[^>]*>\s*</voice>", raw, re.IGNORECASE | re.DOTALL):
            return ""
        return ""

    def _has_voice_markup(self, final_response: str) -> bool:
        raw = str(final_response or "")
        return bool(re.search(r"<voice[^>]*>", raw, re.IGNORECASE))

    def _normalize_outbound_text(self, final_response: str) -> str:
        raw = str(final_response or "")
        if not raw.strip():
            return ""
        without_voice_sections = self._VOICE_SECTION_RE.sub("", raw).strip()
        without_voice_wrappers = re.sub(r"</?voice[^>]*>", "", without_voice_sections, flags=re.IGNORECASE).strip()
        visible = re.sub(r"<[^>]+>", "", without_voice_wrappers).strip()
        if visible:
            return visible
        return ""

    @staticmethod
    def _longest_overlap_suffix_prefix(previous: str, current: str) -> int:
        max_overlap = min(len(previous), len(current))
        for size in range(max_overlap, 0, -1):
            if previous[-size:] == current[:size]:
                return size
        return 0

    def _consume_stream_suffix(self, snapshots: dict[str, str], model_run_id: str, raw_value: str) -> str:
        normalized_run_id = (model_run_id or "").strip() or "__default__"
        current_value = raw_value or ""
        if not current_value:
            return ""

        previous_value = snapshots.get(normalized_run_id, "")
        if not previous_value:
            snapshots[normalized_run_id] = current_value
            return current_value

        if current_value == previous_value:
            return ""

        if current_value.startswith(previous_value):
            suffix = current_value[len(previous_value):]
            snapshots[normalized_run_id] = current_value
            return suffix

        if previous_value.endswith(current_value) or current_value in previous_value:
            return ""

        overlap = self._longest_overlap_suffix_prefix(previous_value, current_value)
        if overlap > 0:
            suffix = current_value[overlap:]
            snapshots[normalized_run_id] = previous_value + suffix
            return suffix

        snapshots[normalized_run_id] = current_value
        return ""

    def _consume_terminal_text_suffix(
        self,
        snapshots: dict[str, str],
        model_run_id: str,
        raw_value: str,
        emitted_text: str,
    ) -> str:
        normalized_run_id = (model_run_id or "").strip() or "__default__"
        current_value = raw_value or ""
        if not current_value:
            return ""

        snapshots[normalized_run_id] = current_value
        if not emitted_text:
            return current_value
        if current_value == emitted_text:
            return ""
        if current_value.startswith(emitted_text):
            return current_value[len(emitted_text):]
        if emitted_text.endswith(current_value) or current_value in emitted_text:
            return ""

        overlap = self._longest_overlap_suffix_prefix(emitted_text, current_value)
        if overlap > 0:
            return current_value[overlap:]
        return ""

    def _extract_delivery_message_id(self, delivery_receipt: Dict[str, Any] | None) -> str | None:
        receipt = dict(delivery_receipt or {})
        queue: list[Any] = [receipt]
        seen_ids: set[int] = set()
        while queue:
            current = queue.pop(0)
            if not isinstance(current, dict):
                continue
            object_id = id(current)
            if object_id in seen_ids:
                continue
            seen_ids.add(object_id)
            for key in ("messageId", "message_id", "id"):
                normalized = str(current.get(key) or "").strip()
                if normalized:
                    return normalized
            for value in current.values():
                if isinstance(value, dict):
                    queue.append(value)
        return None

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "PluginHostRuntime",
            "summary": "负责外部插件渠道消息接入与分发，把标准化消息交给 Supervisor 主链处理。",
            "responsibilities": [
                "标准化外部渠道消息",
                "维护渠道会话与推送记录",
                "把合法消息交给核心对话编排层",
            ],
            "routingKeywords": ["渠道", "机器人", "Webhook", "外部消息", "IM"],
            "acceptedInputs": ["PluginHostMessage", "outbound payload"],
            "producedOutputs": ["channel session", "normalized messages", "push records"],
            "ownedSteps": ["plugin_host.receive", "plugin_host.dispatch", "plugin_host.push"],
            "supportsPause": True,
            "supportsResume": False,
            "supportsApproval": True,
            "supportsRepair": False,
            "visibility": "specialized",
            "promptHints": [
                "外部 IM、Webhook 或插件渠道消息先交给 PluginHostRuntime 标准化，再进入对话编排。",
            ],
            "capabilities": [
                {
                    "key": "plugin_host.dispatch",
                    "label": "插件宿主消息分发",
                    "summary": "负责收发层和会话标准化，不承担复杂业务决策。",
                    "accepts": ["channel payload", "remote identifiers"],
                    "outputs": ["normalized session", "delivery result"],
                    "examples": ["飞书/Telegram/内部 Webhook 消息接入"],
                    "risk_level": "medium",
                }
            ],
        }

    def _resolve_engine_config(self) -> EngineConfig:
        return resolve_engine_config_for_role(
            "channel",
            fallback_provider="deepseek",
            fallback_model="deepseek-chat",
        )["engine_config"]

    def resolve_session_id(
        self,
        source: str,
        remote_id: str,
        chat_type: str,
        *,
        account_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        normalized_source = re.sub(r"[^a-z0-9._-]+", "_", (source or "plugin").strip().lower())
        normalized_chat_type = "group" if str(chat_type).lower() == "group" else "p2p"
        normalized_remote_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", (remote_id or "unknown").strip())
        normalized_account_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(account_id or "").strip()) or "_"
        normalized_thread_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(thread_id or "").strip()) or "_"
        return f"plugin_host:{normalized_source}:{normalized_chat_type}:{normalized_account_id}:{normalized_remote_id}:{normalized_thread_id}"

    def _build_channel_session_metadata(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raw = dict(metadata or {})
        handoff_source = str(raw.get("handoff_source") or raw.get("handoffSource") or "").strip()
        transport_managed_by = str(raw.get("transport_managed_by") or raw.get("transportManagedBy") or "").strip()
        if not transport_managed_by and handoff_source == "openclaw_bridge":
            transport_managed_by = "openclaw_bridge"

        channel_type = str(raw.get("channel_type") or raw.get("channelType") or source or "").strip() or source
        channel_name = str(raw.get("channel_name") or raw.get("channelName") or channel_type or source or "").strip() or channel_type
        channel_domain = str(raw.get("channel_domain") or raw.get("channelDomain") or "").strip()
        account_id = str(raw.get("account_id") or raw.get("accountId") or "").strip()
        default_account = str(raw.get("default_account") or raw.get("defaultAccount") or "").strip()
        bridge_plugin_id = str(raw.get("bridge_plugin_id") or raw.get("bridgePluginId") or "").strip()

        normalized: Dict[str, Any] = {
            "source": source,
            "channel_type": channel_type,
            "channel_name": channel_name,
            "chat_type": chat_type,
            "remote_id": remote_id,
        }
        if channel_domain:
            normalized["channel_domain"] = channel_domain
        if account_id:
            normalized["account_id"] = account_id
        account_scope = str(raw.get("account_scope") or raw.get("accountScope") or "").strip()
        if account_scope:
            normalized["account_scope"] = account_scope
        if default_account:
            normalized["default_account"] = default_account
        if handoff_source:
            normalized["handoff_source"] = handoff_source
        if transport_managed_by:
            normalized["transport_managed_by"] = transport_managed_by
        if bridge_plugin_id:
            normalized["bridge_plugin_id"] = bridge_plugin_id
        thread_id = str(raw.get("thread_id") or raw.get("threadId") or "").strip()
        if thread_id:
            normalized["thread_id"] = thread_id
        event_kind = str(raw.get("event_kind") or raw.get("eventKind") or "").strip()
        if event_kind:
            normalized["event_kind"] = event_kind
        event_subtype = str(raw.get("event_subtype") or raw.get("eventSubtype") or "").strip()
        if event_subtype:
            normalized["event_subtype"] = event_subtype
        mentions = [dict(item) for item in list(raw.get("mentions") or []) if isinstance(item, dict)]
        if mentions:
            normalized["mentions"] = mentions
        attachments = [dict(item) for item in list(raw.get("attachments") or []) if isinstance(item, dict)]
        if attachments:
            normalized["attachments"] = attachments
        raw_action_payload = raw.get("action_payload")
        if raw_action_payload is None:
            raw_action_payload = raw.get("actionPayload")
        action_payload = dict(raw_action_payload) if isinstance(raw_action_payload, dict) else {}
        if action_payload:
            normalized["action_payload"] = action_payload
        channel_envelope = dict(raw.get("channel_envelope") or raw.get("channelEnvelope") or {})
        if channel_envelope:
            normalized["channel_envelope"] = channel_envelope
        raw_payload_ref = dict(raw.get("raw_payload_ref") or raw.get("rawPayloadRef") or {})
        if raw_payload_ref:
            normalized["raw_payload_ref"] = raw_payload_ref
        return normalized

    def _persist_runtime_event(
        self,
        *,
        session_id: str,
        run_id: str,
        topic: str,
        payload: Dict[str, Any],
        node: str,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=session_id,
            run_id=run_id,
            source=RuntimeSource(
                plane="engine",
                component="plugin_host_runtime",
                node=node,
                agent_id=agent_id,
            ),
        )
        return emitter.emit(topic, payload)

    def _refresh_projection_snapshot(self, session_id: str, run_id: Optional[str] = None):
        snapshot_service.refresh_chat_projection(session_id, run_id=run_id)

    def _record_channel_media_artifacts(
        self,
        *,
        session_id: str,
        run_id: str,
        message_id: str,
        channel_type: str,
        asset_payload: Dict[str, Any] | None,
    ) -> None:
        if not isinstance(asset_payload, dict):
            return
        manifest_assets = [
            dict(item)
            for item in list(asset_payload.get("assets") or [])
            if isinstance(item, dict)
        ]
        assets = manifest_assets or ([dict(asset_payload)] if asset_payload else [])
        for index, asset in enumerate(assets):
            file_path = str(asset.get("workspacePath") or asset.get("sourcePath") or "").strip()
            if not file_path:
                continue
            candidate = Path(file_path).expanduser()
            if not candidate.exists() or not candidate.is_file():
                continue
            path_plane = str(asset.get("pathPlane") or asset.get("path_plane") or "").strip() or "runtime_private"
            canonical_path = str(asset.get("canonicalPath") or asset.get("canonical_path") or file_path).strip() or file_path
            storage_class = str(asset.get("storageClass") or asset.get("storage_class") or "").strip() or (
                "ephemeral" if path_plane == "channel_delivery_stage" else "workspace_download"
            )
            title = str(asset.get("originalFileName") or candidate.name or f"attachment_{index + 1}").strip() or f"attachment_{index + 1}"
            subtitle = str(asset.get("displaySubtitle") or canonical_path).strip() or canonical_path
            workspace_path = None if path_plane == "channel_delivery_stage" else file_path
            metadata = {
                "displayLabel": title,
                "displaySubtitle": subtitle,
                "canonicalPath": canonical_path,
                "pathPlane": path_plane,
                "storageClass": storage_class,
                "surfaceVisible": True,
                "channelType": channel_type,
            }
            workspace_root = str(asset.get("workspaceRoot") or asset.get("workspace_root") or "").strip()
            workspace_relative_path = str(asset.get("workspaceRelativePath") or asset.get("workspace_relative_path") or "").strip()
            workspace_id = str(asset.get("workspaceId") or asset.get("workspace_id") or "").strip()
            project_id = str(asset.get("projectId") or asset.get("project_id") or "").strip()
            if workspace_root:
                metadata["workspaceRoot"] = workspace_root
            if workspace_relative_path:
                metadata["workspaceRelativePath"] = workspace_relative_path
            if workspace_id:
                metadata["workspaceId"] = workspace_id
            if project_id:
                metadata["projectId"] = project_id
            artifact_store.record_local_file(
                file_path=candidate,
                session_id=session_id,
                run_id=run_id,
                message_id=message_id,
                workspace_path=workspace_path,
                metadata=metadata,
                source_component="plugin_host_runtime",
                node="plugin_host_runtime",
            )

    def _run_status_is_settled(self, run_handle) -> bool:
        status = str(getattr(getattr(run_handle, "descriptor", None), "status", "") or "").strip().lower()
        return status in {"completed", "failed", "cancelled", "waiting_approval", "waiting_input", "paused"}

    def _record_dispatch_failure(
        self,
        *,
        session_id: str,
        run_id: str,
        run_handle,
        error_message: str,
        error_class: str,
        cancelled: bool,
    ) -> None:
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="plugin_host.dispatch.failed",
            payload={
                "error": error_message,
                "errorClass": error_class,
                "cancelled": cancelled,
            },
            node="plugin_host_runtime",
            agent_id="supervisor",
        )
        if not self._run_status_is_settled(run_handle):
            if cancelled:
                erc_kernel.cancel_run(run_id, reason=error_message)
            else:
                run_handle.fail(error_message, node="plugin_host_runtime")
        self._refresh_projection_snapshot(session_id, run_id)

    def _consume_control_signal(self, *, run_handle) -> Dict[str, Any] | None:
        signal = consume_stop_signal(run_handle.run_id)
        if signal is None:
            return None
        return apply_control_signal(
            run_handle,
            signal=signal,
            runtime_kind="plugin_host",
            node="plugin_host_runtime",
        )

    def record_outbound_push(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        final_msg: str,
        trigger_source: str,
        agent_id: str,
        agent_profile: Dict[str, Any],
        inbound_metadata: Dict[str, Any] | None = None,
        delivery_receipt: Dict[str, Any] | None = None,
        assistant_message_id: str | None = None,
        persist_message: bool = True,
    ) -> str:
        inbound_metadata = dict(inbound_metadata or {})
        session_id = self.resolve_session_id(
            source,
            remote_id,
            chat_type,
            account_id=str(inbound_metadata.get("account_id") or inbound_metadata.get("accountId") or "").strip() or None,
            thread_id=str(inbound_metadata.get("thread_id") or inbound_metadata.get("threadId") or "").strip() or None,
        )
        existing_session = db.get_session(session_id)
        session_metadata = self._build_channel_session_metadata(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            metadata=inbound_metadata,
        )
        channel_label = str(session_metadata.get("channel_name") or source).strip() or source
        title_prefix = f"[{channel_label} {'群聊' if chat_type == 'group' else '单聊'}] "
        db.create_or_update_session(
            session_id=session_id,
            title=(existing_session.get("title") if existing_session else None) or title_prefix + (final_msg[:40] if final_msg else "主动推送"),
            user_id="system",
            metadata={
                **(coerce_json_dict(existing_session.get("metadata")) if existing_session else {}),
                **session_metadata,
            },
        )
        run_id = f"plugin_host_push_{uuid.uuid4().hex}"
        run_handle = erc_kernel.submit_run(
            run_id=run_id,
            session_id=session_id,
            conversation_id=session_id,
            user_id="system",
            runtime_kind="plugin_host_push",
            trigger_source=trigger_source,
            agent_id=agent_id,
            channel_type=source,
            metadata={
                **session_metadata,
                "target_agent": agent_id,
            },
            initial_status="running",
            component="plugin_host_runtime",
            node="plugin_host_runtime",
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="run.created",
            payload={"status": "running", "transport": "plugin_host_push"},
            node="plugin_host_runtime",
            agent_id=agent_id,
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="agent.started",
            payload={
                "agent": {
                    "id": agent_id,
                    "name": agent_profile.get("name"),
                    "avatar": agent_profile.get("avatar"),
                    "roleLabel": agent_profile.get("roleLabel"),
                }
            },
            node=agent_id,
            agent_id=agent_id,
        )
        outbound_message_id = assistant_message_id or str(uuid.uuid4())
        if persist_message:
            db.add_message(
                msg_id=outbound_message_id,
                session_id=session_id,
                role="assistant",
                content=final_msg,
                agent_id=agent_id,
                agent_name=agent_profile.get("name"),
                agent_avatar=agent_profile.get("avatar"),
                agent_role_label=agent_profile.get("roleLabel"),
                metadata={
                    **session_metadata,
                    "push": True,
                    "run_id": run_id,
                    "delivery_receipt": dict(delivery_receipt or {}),
                },
            )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="run.text.delta",
            payload={"type": "text_chunk", "content": final_msg, "message_id": outbound_message_id},
            node=agent_id,
            agent_id=agent_id,
        )
        if delivery_receipt:
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="plugin_host.push.receipt",
                payload={
                    "message_id": outbound_message_id,
                    "deliveryReceipt": dict(delivery_receipt or {}),
                },
                node="plugin_host_runtime",
                agent_id=agent_id,
            )
        run_handle.complete(reason="plugin_host_push_completed", node="plugin_host_runtime")
        return session_id

    def record_media_push(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        trigger_source: str,
        agent_id: str,
        agent_profile: Dict[str, Any],
        inbound_metadata: Dict[str, Any] | None = None,
        delivery_receipt: Dict[str, Any] | None = None,
        visible_content: str | None = None,
        media_delivery: Dict[str, Any] | None = None,
    ) -> str:
        inbound_metadata = dict(inbound_metadata or {})
        session_id = self.resolve_session_id(
            source,
            remote_id,
            chat_type,
            account_id=str(inbound_metadata.get("account_id") or inbound_metadata.get("accountId") or "").strip() or None,
            thread_id=str(inbound_metadata.get("thread_id") or inbound_metadata.get("threadId") or "").strip() or None,
        )
        existing_session = db.get_session(session_id)
        session_metadata = self._build_channel_session_metadata(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            metadata=inbound_metadata,
        )
        channel_label = str(session_metadata.get("channel_name") or source).strip() or source
        title_prefix = f"[{channel_label} {'群聊' if chat_type == 'group' else '单聊'}] "
        seed = visible_content or "媒体发送"
        db.create_or_update_session(
            session_id=session_id,
            title=(existing_session.get("title") if existing_session else None) or title_prefix + seed[:40],
            user_id="system",
            metadata={
                **(coerce_json_dict(existing_session.get("metadata")) if existing_session else {}),
                **session_metadata,
            },
        )
        run_id = f"plugin_host_push_{uuid.uuid4().hex}"
        run_handle = erc_kernel.submit_run(
            run_id=run_id,
            session_id=session_id,
            conversation_id=session_id,
            user_id="system",
            runtime_kind="plugin_host_push",
            trigger_source=trigger_source,
            agent_id=agent_id,
            channel_type=source,
            metadata={
                **session_metadata,
                "target_agent": agent_id,
                "delivery_mode": "media",
            },
            initial_status="running",
            component="plugin_host_runtime",
            node="plugin_host_runtime",
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="run.created",
            payload={"status": "running", "transport": "plugin_host_push", "deliveryMode": "media"},
            node="plugin_host_runtime",
            agent_id=agent_id,
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="agent.started",
            payload={
                "agent": {
                    "id": agent_id,
                    "name": agent_profile.get("name"),
                    "avatar": agent_profile.get("avatar"),
                    "roleLabel": agent_profile.get("roleLabel"),
                }
            },
            node=agent_id,
            agent_id=agent_id,
        )
        media_payload = dict(media_delivery or {})
        receipt_message_id = self._extract_delivery_message_id(delivery_receipt)
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="plugin_host.media.delivery",
            payload={
                "message_id": receipt_message_id,
                "visibleText": str(visible_content or "").strip() or None,
                "deliveryReceipt": dict(delivery_receipt or {}),
                "mediaDelivery": media_payload,
            },
            node="plugin_host_runtime",
            agent_id=agent_id,
        )
        if delivery_receipt:
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="plugin_host.push.receipt",
                payload={
                    "message_id": receipt_message_id,
                    "deliveryReceipt": dict(delivery_receipt or {}),
                    "deliveryMode": "media",
                },
                node="plugin_host_runtime",
                agent_id=agent_id,
            )
        run_handle.complete(reason="plugin_host_media_push_completed", node="plugin_host_runtime")
        return session_id

    async def dispatch_message(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        message: PluginHostMessage,
        audio_trigger: bool = False,
        record_only: bool = False,
    ) -> Tuple[str, Optional[str], str]:
        normalized_inbound = plugin_host_service.normalize_inbound(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            text_content=message.content,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            metadata=message.metadata,
        )
        source = str(normalized_inbound.get("source") or source)
        chat_type = str(normalized_inbound.get("chatType") or chat_type)
        remote_id = str(normalized_inbound.get("remoteId") or remote_id)
        text_content = str(normalized_inbound.get("textContent") or "").strip()
        message.metadata = dict(normalized_inbound.get("metadata") or {})
        if not text_content:
            return "", None, ""

        session_id = self.resolve_session_id(
            source,
            remote_id,
            chat_type,
            account_id=str((message.metadata or {}).get("account_id") or (message.metadata or {}).get("accountId") or "").strip() or None,
            thread_id=str((message.metadata or {}).get("thread_id") or (message.metadata or {}).get("threadId") or "").strip() or None,
        )
        run_id = f"plugin_host_{uuid.uuid4().hex}"
        session_metadata = self._build_channel_session_metadata(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            metadata=message.metadata,
        )

        existing_session = db.get_session(session_id)
        if not existing_session:
            chat_type_label = "群聊" if chat_type == "group" else "单聊"
            channel_display = str(session_metadata.get("channel_name") or source).strip() or source
            title_prefix = f"[{channel_display} {chat_type_label}] "
            title = title_prefix + (text_content[:40] if text_content else "Chat")
        else:
            title = existing_session.get("title")

        db.create_or_update_session(
            session_id=session_id,
            title=title,
            user_id=message.sender_id or "anonymous",
            metadata={
                **(coerce_json_dict(existing_session.get("metadata")) if existing_session else {}),
                **session_metadata,
            },
        )
        existing_binding = session_scope_binding_service.get_binding(session_id)
        scope_result = scope_resolution_service.resolve(
            session_id=session_id,
            conversation_id=session_id,
            user_id=message.sender_id or "anonymous",
            user_query=text_content,
            project_id=(message.metadata or {}).get("project_id"),
            workspace_id=(message.metadata or {}).get("workspace_id"),
            workspace_path=(message.metadata or {}).get("workspace_path"),
            workflow_id=(message.metadata or {}).get("workflow_id"),
            channel_type=source,
            channel_remote_id=remote_id,
            scope_hint=(message.metadata or {}).get("scope_hint"),
            scope_mode="explicit",
            run_id=run_id,
        )
        db.update_session_metadata(
            session_id,
            {
                "project_id": scope_result.binding.project_id,
                "workspace_id": scope_result.binding.workspace_id,
                "workspace_path": scope_result.binding.workspace_path,
                "resolved_scope": scope_result.binding.resolved_scope,
                "scope_source": scope_result.binding.scope_source,
            },
        )
        run_handle = erc_kernel.submit_run(
            run_id=run_id,
            session_id=session_id,
            conversation_id=session_id,
            user_id=message.sender_id or remote_id or "anonymous",
            runtime_kind="plugin_host",
            trigger_source=source,
            agent_id="supervisor",
            channel_type=source,
            metadata={
                **session_metadata,
                "record_only": record_only,
                "resolved_scope": scope_result.binding.resolved_scope,
            },
            initial_status="queued",
            component="plugin_host_runtime",
            node="plugin_host_runtime",
        )
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_metadata = {"transport": "plugin_host", "source": source, "runtimeKind": "plugin_host"}
        lane_decision = await session_admission_service.acquire_async(
            session_id,
            run_id,
            policy=lane_policy,
            runtime_kind="plugin_host",
            metadata=lane_metadata,
        )
        if not lane_decision.acquired:
            busy_run_id = lane_decision.rejected_by_run_id or lane_decision.active_run_id
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="run.lane.rejected",
                payload={
                    "policy": lane_decision.policy,
                    "busy_run_id": busy_run_id,
                    "session_id": session_id,
                },
                node="session_lane",
                agent_id=None,
            )
            erc_kernel.cancel_run(run_id, reason=f"session_lane_busy:{busy_run_id}")
            self._refresh_projection_snapshot(session_id, run_id)
            return (f"当前渠道会话已有任务在执行中，本次消息未进入运行队列。忙碌任务：{busy_run_id}", None, session_id)
        if lane_decision.waited:
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="run.lane.queued",
                payload={
                    "policy": lane_decision.policy,
                    "blocked_by_run_id": lane_decision.active_run_id,
                    "interrupted_run_id": lane_decision.interrupted_run_id,
                },
                node="session_lane",
                agent_id=None,
            )
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="run.liveness.blocked",
                payload={
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": f"lane_busy:{lane_decision.active_run_id}",
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
                node="session_lane",
                agent_id=None,
            )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="run.lane.acquired",
            payload={
                "policy": lane_decision.policy,
                "waited": lane_decision.waited,
                "previous_run_id": lane_decision.active_run_id,
                "interrupted_run_id": lane_decision.interrupted_run_id,
            },
            node="session_lane",
            agent_id=None,
        )
        if lane_decision.waited:
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="run.liveness.recovered",
                payload={
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
                node="session_lane",
                agent_id=None,
            )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="run.created",
            payload={
                "status": "queued",
                "transport": "plugin_host",
                "source": source,
                "chat_type": chat_type,
                "resolved_scope": scope_result.binding.resolved_scope,
                "project_id": scope_result.binding.project_id,
            },
            node="plugin_host_runtime",
        )
        group_guard_decision = assess_channel_inbound_group_risk(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            text_content=text_content,
            metadata=message.metadata,
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="plugin_host.group_guard.checked",
            payload=group_guard_decision.to_payload(),
            node="plugin_host_safety",
            agent_id=None,
        )
        safety_guardian.log_decision_event(
            action="plugin_host_group_guard",
            decision=group_guard_decision,
            subject=message.sender_name or remote_id,
            metadata={"sessionId": session_id, "runId": run_id, "remoteId": remote_id, "source": source},
        )
        if not group_guard_decision.is_allow():
            preflight_block = await self._handle_preflight_block(
                source=source,
                chat_type=chat_type,
                remote_id=remote_id,
                session_id=session_id,
                run_id=run_id,
                scope_result=scope_result,
                run_handle=run_handle,
                preflight_decision=group_guard_decision,
                subject=message.sender_name or remote_id,
            )
            if preflight_block is not None:
                return preflight_block
        preflight_decision = safety_guardian.preflight_runtime(
            runtime_kind="plugin_host",
            trigger_source=source,
            session_id=session_id,
            run_id=run_id,
            resolved_scope=scope_result.binding.resolved_scope,
            user_id=message.sender_id or "anonymous",
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="safety.preflight.checked",
            payload=preflight_decision.to_payload(),
            node="safety_guardian",
            agent_id=None,
        )
        safety_guardian.log_decision_event(
            action="plugin_host_preflight",
            decision=preflight_decision,
            subject=message.sender_name or remote_id,
            metadata={"sessionId": session_id, "runId": run_id, "remoteId": remote_id, "source": source},
        )
        run_handle.transition("running", reason="plugin_host_message_received", node="plugin_host_runtime")
        if not scope_result.reused_existing_binding:
            scope_evidence = dict(scope_result.evidence or {})
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="scope.binding.updated" if existing_binding else "scope.binding.created",
                payload={
                    "session_id": session_id,
                    "project_id": scope_result.binding.project_id,
                    "workspace_id": scope_result.binding.workspace_id,
                    "resolved_scope": scope_result.binding.resolved_scope,
                    "scope_source": scope_result.binding.scope_source,
                    "scope_chain": scope_result.scope_chain,
                    "rebind_reason": str(scope_evidence.get("rebind_reason") or "").strip() or None,
                    "previous_scope": str(scope_evidence.get("previous_scope") or "").strip() or None,
                    "next_scope": str(scope_evidence.get("next_scope") or "").strip() or None,
                    "scope_anchor_comparison": scope_evidence.get("scope_anchor_comparison") if isinstance(scope_evidence.get("scope_anchor_comparison"), dict) else None,
                },
                node="scope_resolution",
                agent_id=None,
            )

        user_message_id = str(uuid.uuid4())
        message_metadata = {
            **build_plugin_host_runtime_metadata(
                source=source,
                chat_type=chat_type,
                remote_id=remote_id,
                sender_id=message.sender_id,
                sender_name=message.sender_name,
                is_master=bool((message.metadata or {}).get("is_master")),
                mentions=(message.metadata or {}).get("mentions") or [],
                wake_triggered=bool((message.metadata or {}).get("wake_triggered")),
            ),
            **dict(message.metadata or {}),
        }
        message_metadata.setdefault("run_id", run_id)
        message_metadata.setdefault("project_id", scope_result.binding.project_id)
        message_metadata.setdefault("workspace_id", scope_result.binding.workspace_id)
        message_metadata.setdefault("workspace_path", scope_result.binding.workspace_path)
        message_metadata.setdefault("workflow_id", scope_result.binding.workflow_id)
        message_metadata.setdefault("resolved_scope", scope_result.binding.resolved_scope)
        message_metadata.setdefault("scope_source", scope_result.binding.scope_source)
        message_metadata.setdefault("scope_chain", list(scope_result.scope_chain or []))
        db.add_message(
            msg_id=user_message_id,
            session_id=session_id,
            role="user",
            content=text_content,
            metadata=message_metadata,
            agent_name=message.sender_name,
        )
        self._record_channel_media_artifacts(
            session_id=session_id,
            run_id=run_id,
            message_id=user_message_id,
            channel_type=source,
            asset_payload=message_metadata.get("message_assets") if isinstance(message_metadata.get("message_assets"), dict) else message_metadata.get("media_asset"),
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="message.user.recorded",
            payload={
                "message_id": user_message_id,
                "content": text_content,
                "images": [],
                "source": source,
                "chat_type": chat_type,
                "sender_name": message.sender_name,
                "sender_id": message.sender_id,
                "metadata": message_metadata,
                "resolved_scope": scope_result.binding.resolved_scope,
            },
            node="plugin_host_runtime",
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="plugin_host.inbound.normalized",
            payload={
                **build_normalized_signal_payload(
                    source_kind="plugin_host",
                    signal_kind="inbound_message",
                    owner_runtime="plugin_host",
                    summary=text_content[:120],
                    related_session_id=session_id,
                    related_run_id=run_id,
                    task_relevant=True,
                    blocking=False,
                    metadata={
                        "source": source,
                        "chat_type": chat_type,
                        "remote_id": remote_id,
                        "sender_id": message.sender_id,
                        "sender_name": message.sender_name,
                    },
                ),
                "source": source,
                "chat_type": chat_type,
                "remote_id": remote_id,
            },
            node="plugin_host_runtime",
        )

        try:
            if not preflight_decision.is_allow():
                preflight_block = await self._handle_preflight_block(
                    source=source,
                    chat_type=chat_type,
                    remote_id=remote_id,
                    session_id=session_id,
                    run_id=run_id,
                    scope_result=scope_result,
                    run_handle=run_handle,
                    preflight_decision=preflight_decision,
                    subject=message.sender_name or remote_id,
                )
                if preflight_block is not None:
                    return preflight_block

            if record_only:
                run_handle.complete(reason="record_only", node="plugin_host_runtime")
                return "", None, session_id

            return await self._run_supervisor_flow(
                source=source,
                chat_type=chat_type,
                remote_id=remote_id,
                session_id=session_id,
                run_id=run_id,
                run_handle=run_handle,
                scope_result=scope_result,
                sender_id=message.sender_id,
                sender_name=message.sender_name,
                audio_trigger=audio_trigger,
                existing_session=existing_session,
                inbound_metadata=message.metadata,
            )
        except asyncio.CancelledError as exc:
            error_message = str(exc).strip() or "PluginHostRuntime run cancelled before completion."
            print(f"[PluginHostRuntime] Dispatch Cancelled: {error_message}")
            traceback.print_exception(exc)
            self._record_dispatch_failure(
                session_id=session_id,
                run_id=run_id,
                run_handle=run_handle,
                error_message=error_message,
                error_class=exc.__class__.__name__,
                cancelled=True,
            )
            raise RuntimeError(error_message) from exc
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            error_message = str(exc).strip() or exc.__class__.__name__
            print(f"[PluginHostRuntime] Dispatch Fatal Error: {error_message}")
            traceback.print_exception(exc)
            self._record_dispatch_failure(
                session_id=session_id,
                run_id=run_id,
                run_handle=run_handle,
                error_message=error_message,
                error_class=exc.__class__.__name__,
                cancelled=False,
            )
            raise RuntimeError(error_message) from exc
        finally:
            await session_admission_service.release_async(
                session_id,
                run_id,
                policy=lane_policy,
                runtime_kind="plugin_host",
                metadata=lane_metadata,
            )
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="run.lane.released",
                payload={"policy": lane_decision.policy, "session_id": session_id},
                node="session_lane",
                agent_id=None,
            )

    async def _handle_preflight_block(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        session_id: str,
        run_id: str,
        scope_result,
        run_handle,
        preflight_decision,
        subject: str,
    ) -> Optional[Tuple[str, Optional[str], str]]:
        if preflight_decision.is_review():
            approval = run_handle.request_approval(
                approval_kind="safety_review",
                request=safety_guardian.build_runtime_preflight_request(
                    runtime_kind="plugin_host",
                    trigger_source=source,
                    decision=preflight_decision,
                    subject=subject,
                ),
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                self._persist_runtime_event(
                    session_id=session_id,
                    run_id=run_id,
                    topic="safety.preflight.auto_approved",
                    payload={
                        "approval_id": approval.get("approval_id"),
                        "policySource": approval.get("policySource"),
                        "reason": preflight_decision.reason,
                        "risk_code": preflight_decision.risk_code,
                        "details": preflight_decision.details,
                    },
                    node="safety_guardian",
                    agent_id=None,
                )
                self._refresh_projection_snapshot(session_id, run_id)
                return None
            self._refresh_projection_snapshot(session_id, run_id)
            return (
                f"Safety Guardian 已暂停本次渠道任务，等待在 Web/Admin 审批后继续。\n审批单号：{approval.get('approval_id')}",
                None,
                session_id,
            )

        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="safety.preflight.blocked",
            payload={
                "reason": preflight_decision.reason,
                "risk_code": preflight_decision.risk_code,
                "details": preflight_decision.details,
            },
            node="safety_guardian",
            agent_id=None,
        )
        run_handle.fail(f"Safety Guardian blocked channel run: {preflight_decision.reason}", node="safety_guardian")
        self._refresh_projection_snapshot(session_id, run_id)
        return (f"Safety Guardian 已阻止本次渠道任务：{preflight_decision.reason}", None, session_id)

    async def _run_supervisor_flow(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        session_id: str,
        run_id: str,
        run_handle,
        scope_result,
        sender_id: str | None,
        sender_name: str | None,
        audio_trigger: bool,
        existing_session,
        inbound_metadata: Dict[str, Any] | None,
    ) -> Tuple[str, Optional[str], str]:
        past_msgs = db.get_messages(session_id)
        from core.storage import storage

        ctx_policy = storage.get_context_config()
        plugin_host_policy = dict((ctx_policy.get("runtime_adapters") or {}).get("plugin_host") or {})
        window_size = int(plugin_host_policy.get("window_size") or 15)
        max_summary_items = int(plugin_host_policy.get("max_summary_items") or 8)
        recent_msgs, older_msgs = select_channel_context_window(past_msgs, max_messages=window_size)
        context_blocks = build_plugin_host_context_blocks(older_msgs, max_items=max_summary_items)

        lc_messages = build_plugin_host_context_messages(
            session_id=session_id,
            chat_type=chat_type,
            recent_messages=recent_msgs,
            context_blocks=context_blocks,
        )

        config = self._resolve_engine_config()

        ctx_config = storage.get_context_config()
        rec_limit = ctx_config.get("recursion_limit", 500)
        bundle = await supervisor_runner.create_execution_bundle(
            config=config,
            messages=lc_messages,
            session_id=session_id,
            recursion_limit=rec_limit,
        )

        output_buffer: list[str] = []
        text_snapshots_by_run: dict[str, str] = {}
        watchdog = GraphStreamWatchdogState()
        supervisor_profile = storage.get_agent_runtime_profile("supervisor")
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="agent.started",
            payload={
                "agent": {
                    "id": "supervisor",
                    "name": supervisor_profile.get("name") or "智能主管",
                    "roleLabel": supervisor_profile.get("roleLabel") or "主理人",
                    "avatar": supervisor_profile.get("avatar") or "",
                }
            },
            node="supervisor",
            agent_id="supervisor",
        )

        try:
            with bind_runtime_context(
                runtime_kind="plugin_host",
                trigger_source=source,
                session_id=session_id,
                run_id=run_id,
                user_id=sender_id or remote_id or "anonymous",
                channel_type=source,
                channel_remote_id=remote_id,
                project_id=scope_result.binding.project_id,
                workspace_id=scope_result.binding.workspace_id,
                resolved_scope=scope_result.binding.resolved_scope,
            ):
                event_stream = supervisor_runner.open_bundle_stream(bundle)
                async with aclosing(event_stream):
                    stream_iter = event_stream.__aiter__()
                    while True:
                        try:
                            event = await next_graph_stream_event(
                                stream_iter,
                                state=watchdog,
                                session_id=session_id,
                                run_id=run_id,
                                on_timeout=lambda payload: (
                                    self._persist_runtime_event(
                                        session_id=session_id,
                                        run_id=run_id,
                                        topic="run.watchdog.stream_idle_timeout",
                                        payload=payload,
                                        node="stream_watchdog",
                                        agent_id=None,
                                    ),
                                    self._persist_runtime_event(
                                        session_id=session_id,
                                        run_id=run_id,
                                        topic="run.liveness.stalled",
                                        payload={
                                            "heartbeat_kind": "stream_watchdog",
                                            "watchdog_source": "stream_watchdog",
                                            "idle_reason": "stream_idle_timeout",
                                            "stalled": True,
                                            **payload,
                                        },
                                        node="stream_watchdog",
                                        agent_id=None,
                                    ),
                                )[-1],
                            )
                        except StopAsyncIteration:
                            break
                        try:
                            controlled = self._consume_control_signal(run_handle=run_handle)
                            if controlled is not None:
                                return "", None, session_id
                            kind = event["event"]
                            data_obj = event.get("data", {})

                            if kind == "on_chain_stream":
                                interrupt_request = _extract_interrupt_request(data_obj.get("chunk"))
                                if interrupt_request and run_handle is not None:
                                    return self._create_waiting_notice(
                                        session_id=session_id,
                                        run_id=run_id,
                                        source=source,
                                        chat_type=chat_type,
                                        scope_result=scope_result,
                                        run_handle=run_handle,
                                        request=interrupt_request,
                                        content="当前渠道任务已暂停，等待您在 Web / Admin 中审批后继续。",
                                    )

                            if kind == "on_chat_model_stream":
                                model_run_id = (event.get("run_id") or "").strip()
                                chunk = data_obj.get("chunk")
                                if chunk:
                                    raw_text, _ = extract_text_and_reasoning(chunk)
                                    if not raw_text and isinstance(chunk, str):
                                        raw_text = chunk
                                    text_delta = self._consume_stream_suffix(text_snapshots_by_run, model_run_id, raw_text)
                                    if text_delta:
                                        watchdog.note_text_progress()
                                        output_buffer.append(text_delta)
                            elif kind == "on_chat_model_end":
                                model_run_id = (event.get("run_id") or "").strip()
                                final_output = data_obj.get("output")
                                raw_text, _ = extract_text_and_reasoning(final_output)
                                if not raw_text and isinstance(final_output, str):
                                    raw_text = final_output
                                text_delta = self._consume_terminal_text_suffix(
                                    text_snapshots_by_run,
                                    model_run_id,
                                    raw_text,
                                    "".join(output_buffer),
                                )
                                if text_delta:
                                    watchdog.note_text_progress()
                                    output_buffer.append(text_delta)
                            elif kind == "on_tool_start":
                                watchdog.note_tool_start(event.get("run_id", ""))
                            elif kind == "on_tool_end":
                                watchdog.note_tool_end(event.get("run_id", ""))
                        finally:
                            watchdog.finish_event(event)

            controlled = self._consume_control_signal(run_handle=run_handle)
            if controlled is not None:
                return "", None, session_id
            final_response = "".join(output_buffer)
            return await self._finalize_success(
                session_id=session_id,
                run_id=run_id,
                run_handle=run_handle,
                source=source,
                chat_type=chat_type,
                remote_id=remote_id,
                final_response=final_response,
                scope_result=scope_result,
                audio_trigger=audio_trigger,
                inbound_metadata=inbound_metadata,
            )
        except ModelGovernanceInterventionRequired as e:
            waiting_notice = self._create_waiting_notice(
                session_id=session_id,
                run_id=run_id,
                source=source,
                chat_type=chat_type,
                scope_result=scope_result,
                run_handle=run_handle,
                request=e.to_request_payload(),
                approval_kind=e.approval_kind,
                content="当前渠道运行触发了模型治理保护，等待您在 Web / Admin 中确认后继续。",
                governance=True,
            )
            if waiting_notice is not None:
                return waiting_notice
            error_message = str(e)
            run_handle.fail(error_message, node="plugin_host_runtime")
            return (
                "当前渠道运行触发了模型治理保护，本次 review 已按默认策略自动批准并记入审计，但当前步骤需要重新触发执行。",
                None,
                session_id,
            )
        except Exception as e:
            print(f"[PluginHostRuntime] Graph Execution Error: {e}")
            import traceback

            traceback.print_exc()
            run_handle.fail(str(e), node="plugin_host_runtime")
            return f"System Error: {str(e)}", None, session_id

    def _create_waiting_notice(
        self,
        *,
        session_id: str,
        run_id: str,
        source: str,
        chat_type: str,
        scope_result,
        run_handle,
        request: Dict[str, Any],
        content: str,
        approval_kind: str | None = None,
        governance: bool = False,
    ) -> Optional[Tuple[str, None, str]]:
        approval = run_handle.request_approval(
            approval_kind=approval_kind or request.get("approvalKind") or "human_input_required",
            request=request,
        )
        if str(approval.get("status") or "").strip().lower() != "pending":
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="approval.auto_approved",
                payload={
                    "approval_id": approval.get("approval_id"),
                    "approval_kind": approval.get("approval_kind"),
                    "policySource": approval.get("policySource"),
                    "request": request,
                },
                node="plugin_host_runtime",
                agent_id="supervisor",
            )
            self._refresh_projection_snapshot(session_id, run_id)
            return None
        assistant_message_id = str(uuid.uuid4())
        metadata = {
            "run_id": run_id,
            "approval_id": approval.get("approval_id"),
            "approval_kind": approval.get("approval_kind"),
            "source": source,
            "chat_type": chat_type,
            "project_id": scope_result.binding.project_id,
            "workspace_id": scope_result.binding.workspace_id,
            "resolved_scope": scope_result.binding.resolved_scope,
        }
        if governance:
            metadata["governance"] = request
        db.add_message(
            msg_id=assistant_message_id,
            session_id=session_id,
            role="assistant",
            content=content,
            metadata=metadata,
            agent_id="supervisor",
            agent_name="智能主管",
            agent_role_label="主理人",
        )
        self._persist_runtime_event(
            session_id=session_id,
            run_id=run_id,
            topic="run.text.delta",
            payload={
                "type": "text_chunk",
                "content": content,
                "message_id": assistant_message_id,
            },
            node="plugin_host_runtime",
            agent_id="supervisor",
        )
        self._refresh_projection_snapshot(session_id, run_id)
        return content, None, session_id

    async def _finalize_success(
        self,
        *,
        session_id: str,
        run_id: str,
        run_handle,
        source: str,
        chat_type: str,
        remote_id: str,
        final_response: str,
        scope_result,
        audio_trigger: bool,
        inbound_metadata: Dict[str, Any] | None,
    ) -> Tuple[str, Optional[str], str]:
        tts_file_path = None
        outbound_response = self._normalize_outbound_text(final_response)
        raw_final_response = str(final_response or "")
        voice_markup_present = self._has_voice_markup(raw_final_response)
        voice_text = self._extract_voice_text(raw_final_response)
        visible_text = str(outbound_response or "").strip()
        final_visible_text = visible_text
        inbound_meta = dict(inbound_metadata or {})
        media_delivery: Dict[str, Any] | None = None
        delivery_receipt: Dict[str, Any] | None = None
        audio_delivery_receipt: Dict[str, Any] | None = None
        degraded_voice = False
        audio_codec = "none"
        fallback_reason = "none"
        if final_response:
            from api.routes import _get_agent_profile

            profile = _get_agent_profile("supervisor")
            assistant_message_id = str(uuid.uuid4())
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="plugin_host.final_response.raw",
                payload={
                    "content": raw_final_response,
                    "hasVoiceMarkup": voice_markup_present,
                },
                node="supervisor",
                agent_id="supervisor",
            )
            audio_settings = {"stt_enabled": True, "tts_mode": "auto"}
            tts_mode = audio_settings.get("tts_mode", "auto")
            voice_delivery_profile = resolve_voice_delivery_profile(source)
            audio_delivery_enabled = voice_profile_allows_audio_delivery(voice_delivery_profile)
            trigger_tts = bool(voice_text) or (tts_mode == "always") or (tts_mode == "auto" and audio_trigger)
            if trigger_tts and audio_delivery_enabled:
                tts_result = await self._generate_tts(final_response, channel_type=source)
                tts_file_path = str((tts_result or {}).get("filePath") or "").strip() or None
                audio_codec = str((tts_result or {}).get("audioCodec") or "none").strip() or "none"
                fallback_reason = str((tts_result or {}).get("fallbackReason") or "none").strip() or "none"
                voice_profile_id = str((tts_result or {}).get("profileId") or "").strip() or None
                voice_mode = str((tts_result or {}).get("voiceMode") or "audio_attachment").strip() or "audio_attachment"
                audio_container = str((tts_result or {}).get("container") or "").strip() or None
                mime_type = str((tts_result or {}).get("mimeType") or "").strip() or None
                as_voice = bool((tts_result or {}).get("asVoice"))
                duration_ms = (tts_result or {}).get("durationMs")
                playtime_ms = (tts_result or {}).get("playtimeMs")
                sample_rate = (tts_result or {}).get("sampleRate")
                bits_per_sample = (tts_result or {}).get("bitsPerSample")
                encode_type = (tts_result or {}).get("encodeType")
                if tts_file_path:
                    plugin_host_service.record_tts_result(
                        audio_codec=audio_codec,
                        fallback_reason=fallback_reason,
                        file_path=tts_file_path,
                    )
                    self._persist_runtime_event(
                        session_id=session_id,
                        run_id=run_id,
                        topic="plugin_host.tts.generated",
                        payload={
                            "filePath": tts_file_path,
                            "source": "voice_markup" if voice_text else "audio_trigger",
                            "audioCodec": audio_codec,
                            "fallbackReason": fallback_reason,
                            "profileId": voice_profile_id,
                            "voiceMode": voice_mode,
                            "container": audio_container,
                            "mimeType": mime_type,
                            "asVoice": as_voice,
                            "durationMs": duration_ms,
                            "playtimeMs": playtime_ms,
                            "sampleRate": sample_rate,
                            "bitsPerSample": bits_per_sample,
                            "encodeType": encode_type,
                        },
                        node="plugin_host_runtime",
                        agent_id="supervisor",
                    )
                    try:
                        audio_delivery_receipt = await plugin_host_service.broadcast_media(
                            channel_type=source,
                            receive_id=remote_id,
                            media_url=tts_file_path,
                            account_id=str(inbound_meta.get("account_id") or "").strip() or None,
                            reply_to_id=str(inbound_meta.get("message_id") or "").strip() or None,
                            thread_id=str(inbound_meta.get("thread_id") or "").strip() or None,
                            tts_meta={
                                "deliveryMode": voice_mode,
                                "voiceMode": voice_mode,
                                "assetKind": "audio",
                                "audioCodec": audio_codec,
                                "fallbackReason": fallback_reason,
                                "profileId": voice_profile_id,
                                "container": audio_container,
                                "mimeType": mime_type,
                                "asVoice": as_voice,
                                "durationMs": duration_ms,
                                "playtimeMs": playtime_ms,
                                "sampleRate": sample_rate,
                                "bitsPerSample": bits_per_sample,
                                "encodeType": encode_type,
                                "fileName": os.path.basename(tts_file_path),
                            },
                        )
                        media_delivery = {
                            "kind": voice_mode,
                            "source": "tts",
                            "filePath": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("workspacePath") or tts_file_path or "").strip() or None,
                            "deliveryMode": voice_mode,
                            "audioCodec": audio_codec,
                            "fallbackReason": fallback_reason,
                            "profileId": voice_profile_id,
                            "container": audio_container,
                            "mimeType": mime_type,
                            "asVoice": as_voice,
                            "durationMs": duration_ms,
                            "playtimeMs": playtime_ms,
                            "sampleRate": sample_rate,
                            "bitsPerSample": bits_per_sample,
                            "encodeType": encode_type,
                            "workspacePath": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("workspacePath") or "").strip() or None,
                            "canonicalPath": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("canonicalPath") or "").strip() or None,
                            "pathPlane": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("pathPlane") or "").strip() or None,
                            "storageClass": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("storageClass") or "").strip() or None,
                            "originalFileName": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("originalFileName") or "").strip() or None,
                        }
                        self._persist_runtime_event(
                            session_id=session_id,
                            run_id=run_id,
                            topic="plugin_host.audio.delivery",
                            payload={
                                "deliveryMode": voice_mode,
                                "filePath": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("workspacePath") or tts_file_path or "").strip() or None,
                                "visibleText": visible_text or None,
                                "audioCodec": audio_codec,
                                "fallbackReason": fallback_reason,
                                "profileId": voice_profile_id,
                                "container": audio_container,
                                "mimeType": mime_type,
                                "asVoice": as_voice,
                                "durationMs": duration_ms,
                                "playtimeMs": playtime_ms,
                                "sampleRate": sample_rate,
                                "bitsPerSample": bits_per_sample,
                                "encodeType": encode_type,
                                "deliveryReceipt": dict(audio_delivery_receipt or {}),
                            },
                            node="plugin_host_runtime",
                            agent_id="supervisor",
                        )
                    except Exception as audio_exc:
                        degraded_voice = True
                        self._persist_runtime_event(
                            session_id=session_id,
                            run_id=run_id,
                            topic="plugin_host.tts.degraded",
                            payload={
                                "reason": str(audio_exc).strip() or "audio_delivery_failed",
                                "filePath": tts_file_path,
                                "deliveryMode": voice_mode,
                                "audioCodec": audio_codec,
                                "fallbackReason": fallback_reason,
                                "profileId": voice_profile_id,
                            },
                            node="plugin_host_runtime",
                            agent_id="supervisor",
                        )
                elif voice_markup_present:
                    degraded_voice = True
                    self._persist_runtime_event(
                        session_id=session_id,
                        run_id=run_id,
                        topic="plugin_host.tts.degraded",
                        payload={
                            "reason": "tts_generation_failed",
                            "deliveryMode": "audio_attachment",
                            "audioCodec": audio_codec,
                            "fallbackReason": fallback_reason,
                        },
                        node="plugin_host_runtime",
                        agent_id="supervisor",
                    )
            elif trigger_tts and not audio_delivery_enabled:
                degraded_voice = True
                voice_profile_id = str(voice_delivery_profile.get("profileId") or "").strip() or None
                voice_mode = str(voice_delivery_profile.get("mode") or "text_only").strip() or "text_only"
                fallback_reason = "audio_delivery_disabled"
                self._persist_runtime_event(
                    session_id=session_id,
                    run_id=run_id,
                    topic="plugin_host.tts.degraded",
                    payload={
                        "reason": "audio_delivery_disabled",
                        "deliveryMode": voice_mode,
                        "profileId": voice_profile_id,
                        "channelType": source,
                    },
                    node="plugin_host_runtime",
                    agent_id="supervisor",
                )
            elif voice_markup_present:
                degraded_voice = True
                self._persist_runtime_event(
                    session_id=session_id,
                    run_id=run_id,
                    topic="plugin_host.tts.degraded",
                    payload={
                        "reason": "empty_voice_block" if not voice_text else "tts_skipped",
                        "deliveryMode": "audio_attachment",
                        "audioCodec": audio_codec,
                        "fallbackReason": fallback_reason,
                    },
                    node="plugin_host_runtime",
                    agent_id="supervisor",
                )

            if not final_visible_text and voice_markup_present and not audio_delivery_receipt:
                final_visible_text = voice_text or "抱歉，这次语音回复生成失败，请稍后重试。"

            if final_visible_text:
                delivery_receipt = await plugin_host_service.broadcast_text(
                    channel_type=source,
                    receive_id=remote_id,
                    text=final_visible_text,
                    account_id=str(inbound_meta.get("account_id") or "").strip() or None,
                    reply_to_id=str(inbound_meta.get("message_id") or "").strip() or None,
                    thread_id=str(inbound_meta.get("thread_id") or "").strip() or None,
                )

            display_content = final_visible_text or ("（已发送语音附件）" if audio_delivery_receipt else "")
            db.add_message(
                msg_id=assistant_message_id,
                session_id=session_id,
                role="assistant",
                content=display_content,
                metadata={
                    "run_id": run_id,
                    "source": source,
                    "chat_type": chat_type,
                    "project_id": scope_result.binding.project_id,
                    "workspace_id": scope_result.binding.workspace_id,
                    "resolved_scope": scope_result.binding.resolved_scope,
                    "account_id": inbound_meta.get("account_id"),
                    "reply_to_id": inbound_meta.get("message_id"),
                    "raw_final_response": raw_final_response,
                    "visible_text": final_visible_text,
                    "tts_file_path": str((audio_delivery_receipt or {}).get("mediaAsset", {}).get("workspacePath") or tts_file_path or "").strip() or None,
                    "audio_codec": audio_codec,
                    "audio_fallback_reason": fallback_reason,
                    "voice_profile_id": voice_profile_id if trigger_tts else None,
                    "voice_mode": voice_mode if trigger_tts else None,
                    "audio_container": audio_container if trigger_tts else None,
                    "audio_mime_type": mime_type if trigger_tts else None,
                    "audio_duration_ms": duration_ms if trigger_tts else None,
                    "audio_playtime_ms": playtime_ms if trigger_tts else None,
                    "audio_sample_rate": sample_rate if trigger_tts else None,
                    "audio_bits_per_sample": bits_per_sample if trigger_tts else None,
                    "audio_encode_type": encode_type if trigger_tts else None,
                    "audio_delivery": dict(media_delivery or {}),
                    "degraded_voice": degraded_voice,
                },
                agent_id="supervisor",
                agent_name=profile["name"],
                agent_avatar=profile["avatar"],
                agent_role_label=profile["roleLabel"],
            )
            if audio_delivery_receipt:
                self._record_channel_media_artifacts(
                    session_id=session_id,
                    run_id=run_id,
                    message_id=assistant_message_id,
                    channel_type=source,
                    asset_payload=dict((audio_delivery_receipt or {}).get("mediaAsset") or {}),
                )
            self._persist_runtime_event(
                session_id=session_id,
                run_id=run_id,
                topic="plugin_host.final_response.visible",
                payload={
                    "content": final_visible_text,
                    "displayContent": display_content or None,
                    "degradedVoice": degraded_voice,
                    "audioCodec": audio_codec,
                    "fallbackReason": fallback_reason,
                    "voiceMode": voice_mode if trigger_tts else None,
                    "profileId": voice_profile_id if trigger_tts else None,
                },
                node="supervisor",
                agent_id="supervisor",
            )
            if display_content:
                self._persist_runtime_event(
                    session_id=session_id,
                    run_id=run_id,
                    topic="run.text.delta",
                    payload={
                        "type": "text_chunk",
                        "content": display_content,
                        "message_id": assistant_message_id,
                    },
                    node="supervisor",
                    agent_id="supervisor",
                )
            if delivery_receipt:
                self.record_outbound_push(
                    source=source,
                    chat_type=chat_type,
                    remote_id=remote_id,
                    final_msg=final_visible_text,
                    trigger_source=source,
                    agent_id="supervisor",
                    agent_profile=profile,
                    inbound_metadata=inbound_meta,
                    delivery_receipt=delivery_receipt,
                    assistant_message_id=assistant_message_id,
                    persist_message=False,
                )
            if audio_delivery_receipt:
                self.record_media_push(
                    source=source,
                    chat_type=chat_type,
                    remote_id=remote_id,
                    trigger_source=source,
                    agent_id="supervisor",
                    agent_profile=profile,
                    inbound_metadata=inbound_meta,
                    delivery_receipt=audio_delivery_receipt,
                    visible_content=final_visible_text,
                    media_delivery=media_delivery,
                )

        try:
            from core.terminal_post_run import terminal_post_run_service

            asyncio.create_task(
                asyncio.to_thread(
                    terminal_post_run_service.dispatch,
                    session_id=session_id,
                    run_id=run_id,
                    source_component="plugin_host_runtime",
                )
            )
        except Exception as hook_e:
            print(f"[PluginHostRuntime] Error triggering on_chat_end hook: {hook_e}")

        run_handle.complete(reason="plugin_host_dispatch_finished", node="plugin_host_runtime")
        if voice_markup_present and not final_visible_text and not audio_delivery_receipt:
            return voice_text or "抱歉，这次语音回复生成失败，请稍后重试。", tts_file_path, session_id
        if final_visible_text:
            return final_visible_text, tts_file_path, session_id
        if audio_delivery_receipt:
            return "（已发送语音附件）", tts_file_path, session_id
        return "", tts_file_path, session_id

    async def _generate_tts(self, final_response: str, *, channel_type: str) -> Optional[Dict[str, Any]]:
        try:
            from core.audio.tts_provider import TTSManager

            tts_paths = build_tts_output_paths(stem=f"reply_{int(time.time() * 1000)}")
            mp3_path = str(tts_paths["mp3"])
            tts_provider = TTSManager.get_provider()
            voice_profile = resolve_voice_delivery_profile(channel_type)
            fallback_profile = resolve_voice_delivery_profile(voice_profile.get("fallbackProfileId"))
            delivery_requirements = dict(voice_profile.get("deliveryRequirements") or voice_profile)
            fallback_requirements = dict(fallback_profile.get("deliveryRequirements") or fallback_profile)
            encode_preset = resolve_voice_encode_preset(voice_profile)
            fallback_encode_preset = resolve_voice_encode_preset(fallback_profile)
            ffmpeg_executable = shutil.which("ffmpeg")
            ffprobe_executable = shutil.which("ffprobe")

            def _as_int(value: Any, *, default: int | None = None) -> int | None:
                try:
                    if value is None or value == "":
                        return default
                    return int(value)
                except Exception:
                    return default

            def _build_tts_result(
                *,
                file_path: str | None,
                profile: dict[str, Any],
                requirements: dict[str, Any],
                audio_codec: str,
                fallback_reason: str,
                duration_ms: int | None,
                sample_rate: int | None = None,
                bits_per_sample: int | None = None,
                encode_type: int | None = None,
                toolchain: dict[str, Any] | None = None,
                silk_validation: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                result: dict[str, Any] = {
                    "filePath": file_path,
                    "audioCodec": audio_codec,
                    "fallbackReason": fallback_reason,
                    "profileId": str(profile.get("profileId") or "audio_attachment"),
                    "voiceMode": str(profile.get("mode") or "audio_attachment"),
                    "container": str(profile.get("container") or "mp3"),
                    "mimeType": str(profile.get("mimeType") or "audio/mpeg"),
                    "asVoice": bool(profile.get("asVoice")),
                }
                if duration_ms is not None:
                    result["durationMs"] = int(duration_ms)
                    if bool(requirements.get("requiresPlaytime")):
                        result["playtimeMs"] = int(duration_ms)
                if sample_rate is not None:
                    result["sampleRate"] = int(sample_rate)
                if bits_per_sample is not None:
                    result["bitsPerSample"] = int(bits_per_sample)
                if encode_type is not None:
                    result["encodeType"] = int(encode_type)
                if toolchain is not None:
                    result["toolchain"] = toolchain
                if silk_validation is not None:
                    result["silkValidation"] = silk_validation
                return result

            voice_blocks = re.findall(r"<voice[^>]*>(.*?)</voice>", final_response, re.IGNORECASE | re.DOTALL)
            clean_tts_text = " ".join([b.strip() for b in voice_blocks]) if voice_blocks else final_response
            clean_tts_text = re.sub(r"!\[.*?\]\(.*?\)", "", clean_tts_text)
            clean_tts_text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", clean_tts_text)
            clean_tts_text = re.sub(r"<[^>]+>", "", clean_tts_text)
            clean_tts_text = clean_tts_text.replace("*", "").replace("#", "").strip()

            if clean_tts_text:
                audio_bytes = bytearray()
                async for chunk in tts_provider.synthesize_stream(clean_tts_text):
                    audio_bytes.extend(chunk)
                if audio_bytes:
                    with open(mp3_path, "wb") as f:
                        f.write(audio_bytes)
                    duration_ms = probe_audio_duration_ms(
                        mp3_path,
                        ffprobe_executable=ffprobe_executable,
                    )
                    if voice_profile_requires_external_encoder(voice_profile):
                        toolchain = str(voice_profile.get("toolchain") or "").strip().lower()
                        if toolchain == "silk_v3":
                            try:
                                target_sample_rate = _as_int(
                                    delivery_requirements.get("sampleRate"),
                                    default=_as_int(encode_preset.get("sampleRate"), default=16000),
                                )
                                target_bitrate = _as_int(
                                    encode_preset.get("bitrate"),
                                    default=18000,
                                )
                                bits_per_sample = _as_int(
                                    delivery_requirements.get("bitsPerSample"),
                                    default=_as_int(encode_preset.get("bitsPerSample"), default=16),
                                )
                                encode_type = _as_int(
                                    delivery_requirements.get("encodeType"),
                                    default=6,
                                )
                                silk_meta = encode_audio_to_silk(
                                    source_audio_path=mp3_path,
                                    output_path=tts_paths["silk"],
                                    sample_rate=int(target_sample_rate or 16000),
                                    bitrate=int(target_bitrate or 18000),
                                    ffmpeg_executable=ffmpeg_executable,
                                )
                                silk_validation = validate_and_normalize_tencent_silk(
                                    file_path=str(silk_meta["filePath"]),
                                )
                                return _build_tts_result(
                                    file_path=str(silk_meta["filePath"]),
                                    profile=voice_profile,
                                    requirements=delivery_requirements,
                                    audio_codec=str(voice_profile.get("codec") or "silk_v3"),
                                    fallback_reason="none",
                                    duration_ms=duration_ms,
                                    sample_rate=target_sample_rate,
                                    bits_per_sample=bits_per_sample,
                                    encode_type=encode_type,
                                    toolchain=silk_toolchain_status(),
                                    silk_validation=silk_validation,
                                )
                            except SilkCodecError as silk_error:
                                return _build_tts_result(
                                    file_path=mp3_path if os.path.exists(mp3_path) else None,
                                    profile=fallback_profile,
                                    requirements=fallback_requirements,
                                    audio_codec=str(fallback_profile.get("codec") or "mp3"),
                                    fallback_reason=silk_error.reason,
                                    duration_ms=duration_ms,
                                    sample_rate=_as_int(fallback_encode_preset.get("sampleRate")),
                                    bits_per_sample=_as_int(fallback_encode_preset.get("bitsPerSample")),
                                    toolchain=silk_toolchain_status(),
                                )
                        return _build_tts_result(
                            file_path=mp3_path if os.path.exists(mp3_path) else None,
                            profile=fallback_profile,
                            requirements=fallback_requirements,
                            audio_codec=str(fallback_profile.get("codec") or "mp3"),
                            fallback_reason="external_encoder_missing",
                            duration_ms=duration_ms,
                            sample_rate=_as_int(fallback_encode_preset.get("sampleRate")),
                            bits_per_sample=_as_int(fallback_encode_preset.get("bitsPerSample")),
                        )
                    if not ffmpeg_executable:
                        return _build_tts_result(
                            file_path=mp3_path if os.path.exists(mp3_path) else None,
                            profile=fallback_profile,
                            requirements=fallback_requirements,
                            audio_codec=str(fallback_profile.get("codec") or "mp3"),
                            fallback_reason="ffmpeg_missing",
                            duration_ms=duration_ms,
                            sample_rate=_as_int(fallback_encode_preset.get("sampleRate")),
                            bits_per_sample=_as_int(fallback_encode_preset.get("bitsPerSample")),
                        )
                    target_container = str(voice_profile.get("container") or "mp3").strip().lower()
                    if target_container == "mp3":
                        return _build_tts_result(
                            file_path=mp3_path,
                            profile=voice_profile,
                            requirements=delivery_requirements,
                            audio_codec=str(voice_profile.get("codec") or "mp3"),
                            fallback_reason="none",
                            duration_ms=duration_ms,
                        )
                    target_path = str(
                        tts_paths["ogg" if target_container == "ogg" else "opus" if target_container == "opus" else "mp3"]
                    )
                    bitrate_value = _as_int(encode_preset.get("bitrate"), default=32000) or 32000
                    sample_rate = _as_int(encode_preset.get("sampleRate"), default=24000) or 24000
                    channels = _as_int(encode_preset.get("channels"), default=1) or 1
                    bits_per_sample = _as_int(
                        delivery_requirements.get("bitsPerSample"),
                        default=_as_int(encode_preset.get("bitsPerSample"), default=16),
                    )
                    codec = str(voice_profile.get("codec") or "mp3").strip().lower()
                    ffmpeg_args = [
                        ffmpeg_executable,
                        "-y",
                        "-i",
                        mp3_path,
                        "-ar",
                        str(sample_rate),
                        "-ac",
                        str(channels),
                    ]
                    if codec == "opus":
                        ffmpeg_args.extend(
                            [
                                "-acodec",
                                "libopus",
                                "-b:a",
                                f"{max(8, bitrate_value // 1000)}k",
                                "-vbr",
                                "on",
                                "-compression_level",
                                "10",
                            ]
                        )
                    else:
                        ffmpeg_args.extend(["-b:a", f"{max(16, bitrate_value // 1000)}k"])
                    ffmpeg_args.append(target_path)
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            *ffmpeg_args,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.communicate()
                        if os.path.exists(target_path):
                            output_duration_ms = probe_audio_duration_ms(
                                target_path,
                                ffprobe_executable=ffprobe_executable,
                            ) or duration_ms
                            return _build_tts_result(
                                file_path=target_path,
                                profile=voice_profile,
                                requirements=delivery_requirements,
                                audio_codec=str(voice_profile.get("codec") or "mp3"),
                                fallback_reason="none",
                                duration_ms=output_duration_ms,
                                sample_rate=sample_rate,
                                bits_per_sample=bits_per_sample,
                            )
                        if os.path.exists(mp3_path):
                            return _build_tts_result(
                                file_path=mp3_path,
                                profile=fallback_profile,
                                requirements=fallback_requirements,
                                audio_codec=str(fallback_profile.get("codec") or "mp3"),
                                fallback_reason="ffmpeg_failed",
                                duration_ms=duration_ms,
                                sample_rate=_as_int(fallback_encode_preset.get("sampleRate")),
                                bits_per_sample=_as_int(fallback_encode_preset.get("bitsPerSample")),
                            )
                    except Exception as e_ffmpeg:
                        print(f"[PluginHostRuntime] FFmpeg exception ({type(e_ffmpeg).__name__}): {e_ffmpeg!r}")
                        if os.path.exists(mp3_path):
                            return _build_tts_result(
                                file_path=mp3_path,
                                profile=fallback_profile,
                                requirements=fallback_requirements,
                                audio_codec=str(fallback_profile.get("codec") or "mp3"),
                                fallback_reason="ffmpeg_failed",
                                duration_ms=duration_ms,
                                sample_rate=_as_int(fallback_encode_preset.get("sampleRate")),
                                bits_per_sample=_as_int(fallback_encode_preset.get("bitsPerSample")),
                            )
        except Exception as e_tts:
            print(f"[PluginHostRuntime] TTS exception: {e_tts}")
        return None


ChannelMessage = PluginHostMessage

plugin_host_runtime = runtime_registry.register(PluginHostRuntime())
