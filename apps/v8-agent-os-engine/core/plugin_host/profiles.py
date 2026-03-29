from __future__ import annotations

from typing import Any


def _field(
    key: str,
    *,
    label: str,
    field_type: str = "string",
    required: bool = False,
    help_text: str | None = None,
    enum: list[str] | None = None,
    fmt: str | None = None,
    secret: bool = False,
    scope: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": "secret" if secret else field_type,
        "required": required,
        "help": help_text,
        "enum": list(enum or []) or None,
        "format": fmt,
        "scope": str(scope or "").strip() or None,
    }


_PLUGIN_PROFILE_ALIASES = {
    "openclaw-weixin": "weixin",
    "weixin": "weixin",
    "discord": "discord",
    "@openclaw/discord": "discord",
    "feishu": "feishu",
    "lark": "feishu",
    "@openclaw/feishu": "feishu",
    "openclaw-lark": "feishu",
    "@larksuite/openclaw-lark": "feishu",
    "@larksuite/openclaw-lark-tools": "lark-tools-helper",
    "openclaw-lark-tools": "lark-tools-helper",
}

_FEISHU_TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "feishu_doc", "description": "读写飞书文档内容与结构。"},
    {"name": "feishu_chat", "description": "查询飞书会话、成员与消息上下文。"},
    {"name": "feishu_wiki", "description": "浏览飞书知识库与节点结构。"},
    {"name": "feishu_drive", "description": "访问飞书云盘文件与目录。"},
    {"name": "feishu_perm", "description": "管理飞书文档与文件权限。"},
    {"name": "feishu_bitable_get_meta", "description": "读取多维表格元数据。"},
    {"name": "feishu_bitable_list_fields", "description": "列出多维表格字段。"},
    {"name": "feishu_bitable_list_records", "description": "列出多维表格记录。"},
    {"name": "feishu_bitable_get_record", "description": "读取多维表格单条记录。"},
    {"name": "feishu_bitable_create_record", "description": "创建多维表格记录。"},
    {"name": "feishu_bitable_update_record", "description": "更新多维表格记录。"},
    {"name": "feishu_bitable_create_app", "description": "创建新的多维表格应用。"},
    {"name": "feishu_bitable_create_field", "description": "创建多维表格字段。"},
    {"name": "feishu_app_scopes", "description": "查询飞书应用 scopes 与授权范围。"},
]

_PROFILE_DEFS: dict[str, dict[str, Any]] = {
    "weixin": {
        "renderMode": "config_schema",
        "renderableFields": [],
        "onboardingType": "qr_login",
        "actionMode": "qr_link",
        "manualSteps": [
            "点击“重新接入”重新拉起微信登录/扫码流程。",
            "如果页面没有新的二维码或链接，请查看最新接入任务与 OpenClaw 日志。",
            "登录成功后再刷新健康状态，确认 gateway / RPC 已就绪。",
        ],
        "requiredSecrets": [],
        "requiredIds": [],
        "pairingMode": "qr_login",
        "chatTypes": ["direct"],
        "groupSupported": False,
        "supportTier": "transport-hosted",
        "executionSupport": "v8_handoff",
        "familyAdapterReady": True,
        "callableTools": [],
        "registrationMode": "transport_only",
        "audioOutbound": "attachment",
        "audioInbound": "transcript_capable",
        "fileOutbound": "attachment",
        "voiceDeliveryMode": "attachment",
    },
    "discord": {
        "renderMode": "wizard_introspection",
        "renderableFields": [
            _field(
                "token",
                label="Discord Bot Token",
                required=True,
                secret=True,
                help_text="Bot token。默认建议填在账号级配置里，便于多账号接入。",
            ),
            _field(
                "dmPolicy",
                label="私聊策略",
                field_type="string",
                enum=["pairing", "allowlist", "open"],
                help_text="控制 Discord 私聊默认如何唤醒机器人。",
            ),
            _field(
                "allowFrom",
                label="私聊 Allowlist",
                help_text="逗号分隔的用户名或用户 ID。适合和私聊 allowlist 搭配使用。",
                fmt="csv_list",
            ),
            _field(
                "groupPolicy",
                label="群聊/频道策略",
                field_type="string",
                enum=["allowlist", "open", "disabled"],
                help_text="控制群聊/频道中默认是否响应。",
            ),
            _field(
                "groupAllowFrom",
                label="群聊/频道 Allowlist",
                help_text="逗号分隔的 guild/channel 条目，例如 guild:123 或 guild/channel。",
                fmt="csv_list",
                scope="shared",
            ),
        ],
        "onboardingType": "mixed",
        "actionMode": "manual_steps",
        "manualSteps": [
            "先在 Discord Developer Portal 创建 Bot，并开启所需 intents。",
            "把 Bot Token 填入下方配置卡；如需群聊/频道接入，再补 Server ID、User ID 等 ID 信息。",
            "启动 gateway 后，通过 DM pairing code 完成最终授权。",
        ],
        "docsUrl": "https://docs.openclaw.ai/channels/discord",
        "requiredSecrets": ["token"],
        "requiredIds": ["serverId", "userId"],
        "pairingMode": "dm_code",
        "chatTypes": ["direct", "group", "thread"],
        "groupSupported": True,
        "supportTier": "handoff unsupported",
        "executionSupport": "execution unsupported",
        "familyAdapterReady": False,
        "callableTools": [],
        "registrationMode": "transport_only",
        "audioOutbound": "native_voice",
        "audioInbound": "file_only",
        "fileOutbound": "attachment",
        "voiceDeliveryMode": "native_voice",
    },
    "feishu": {
        "renderMode": "wizard_introspection",
        "renderableFields": [
            _field("appId", label="App ID", required=True, help_text="飞书/Lark 自建应用 App ID。"),
            _field("appSecret", label="App Secret", required=True, secret=True, help_text="飞书/Lark 自建应用 App Secret。"),
            _field(
                "connectionMode",
                label="连接方式",
                field_type="string",
                enum=["websocket", "webhook"],
                help_text="WebSocket 为默认推荐；Webhook 适合企业回调接入。",
            ),
            _field(
                "verificationToken",
                label="Verification Token",
                secret=True,
                help_text="Webhook 模式需要的验证 token。",
            ),
            _field(
                "encryptKey",
                label="Encrypt Key",
                secret=True,
                help_text="Webhook 加密回调时使用的 Encrypt Key。",
            ),
            _field(
                "domain",
                label="域名区域",
                field_type="string",
                enum=["feishu", "lark"],
                help_text="中国区用 Feishu，国际版用 Lark。",
            ),
            _field(
                "dmPolicy",
                label="私聊策略",
                field_type="string",
                enum=["pairing", "allowlist", "open"],
                help_text="控制私聊默认如何唤醒机器人。",
            ),
            _field(
                "allowFrom",
                label="私聊 Allowlist",
                help_text="逗号分隔的 open_id / user_id。",
                fmt="csv_list",
            ),
            _field(
                "groupPolicy",
                label="群聊策略",
                field_type="string",
                enum=["allowlist", "open", "disabled"],
                help_text="控制群聊默认是否响应。",
            ),
            _field(
                "groupAllowFrom",
                label="群聊 Allowlist",
                help_text="逗号分隔的 chat_id，仅在群聊策略为 allowlist 时生效。",
                fmt="csv_list",
            ),
        ],
        "onboardingType": "setup_wizard",
        "actionMode": "config_form",
        "manualSteps": [
            "先填写 App ID / App Secret 以及连接方式。",
            "Webhook 模式还需要 Verification Token / Encrypt Key。",
            "保存配置后，再按页面提示继续 wizard / 授权流程。",
        ],
        "docsUrl": "https://github.com/larksuite/openclaw-lark",
        "requiredSecrets": ["appId", "appSecret"],
        "requiredIds": [],
        "pairingMode": "wizard",
        "chatTypes": ["direct", "group"],
        "groupSupported": True,
        "supportTier": "handoff unsupported",
        "executionSupport": "plugin_tools_proxy",
        "familyAdapterReady": True,
        "callableTools": _FEISHU_TOOL_DEFS,
        "registrationMode": "plugin_host_registry",
        "audioOutbound": "native_voice",
        "audioInbound": "file_only",
        "fileOutbound": "attachment",
        "voiceDeliveryMode": "native_voice",
    },
    "lark-tools-helper": {
        "renderMode": "catalog_helper",
        "renderableFields": [],
        "onboardingType": "config_only",
        "actionMode": "manual_steps",
        "manualSteps": ["当前包主要承担安装/接入辅助，不作为可直接调用的原生插件样板。"],
        "requiredSecrets": [],
        "requiredIds": [],
        "pairingMode": "none",
        "chatTypes": [],
        "groupSupported": False,
        "supportTier": "registered only",
        "executionSupport": "execution unsupported",
        "familyAdapterReady": False,
        "callableTools": [],
        "registrationMode": "helper_only",
        "audioOutbound": "none",
        "audioInbound": "none",
        "fileOutbound": "none",
        "voiceDeliveryMode": "unsupported",
    },
}

_BUILTIN_INSTALL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "openclaw-weixin",
        "label": "安装微信 Channels 插件",
        "description": "推荐入口。安装后由 PluginHostRuntime 负责 transport / inbound handoff / outbound。",
        "pluginTypeHint": "channel",
        "family": "channel",
        "installerCommand": "npx -y @tencent-weixin/openclaw-weixin-cli@latest install",
        "installSpec": "@tencent-weixin/openclaw-weixin",
        "source": "builtin",
    },
    {
        "id": "openclaw-discord",
        "label": "安装 Discord Channels 样板",
        "description": "transport/config 样板，适合验证账号、群聊、线程与 setup 向导表单。",
        "pluginTypeHint": "channel",
        "family": "channel",
        "installSpec": "@openclaw/discord",
        "source": "builtin",
    },
    {
        "id": "openclaw-lark",
        "label": "安装 Lark/Feishu 混合样板",
        "description": "channel + native tools 样板，适合验证动态配置卡片和 PluginHost 原生工具代理。",
        "pluginTypeHint": "channel",
        "family": "channel",
        "installSpec": "@larksuite/openclaw-lark",
        "source": "builtin",
    },
]


def resolve_plugin_profile_key(*, plugin_id: str | None, package_manifest: dict[str, Any] | None = None) -> str | None:
    candidates: list[str] = []
    normalized_id = str(plugin_id or "").strip()
    if normalized_id:
        candidates.append(normalized_id)
    package_name = ""
    if isinstance(package_manifest, dict):
        package_name = str(package_manifest.get("name") or "").strip()
        if package_name:
            candidates.append(package_name)
        openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
        channel_meta = openclaw_meta.get("channel") if isinstance(openclaw_meta, dict) else {}
        channel_id = str((channel_meta or {}).get("id") or "").strip()
        if channel_id:
            candidates.append(channel_id)
        aliases = [str(item).strip() for item in list((channel_meta or {}).get("aliases") or []) if str(item).strip()]
        candidates.extend(aliases)
    for candidate in candidates:
        resolved = _PLUGIN_PROFILE_ALIASES.get(candidate.lower())
        if resolved:
            return resolved
    return None


def _coerce_profile_inputs(
    plugin: dict[str, Any] | str | None = None,
    *,
    plugin_id: str | None = None,
    package_manifest: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(plugin, dict):
        package_candidate = package_manifest
        if not isinstance(package_candidate, dict):
            package_candidate = plugin.get("packageManifest") if isinstance(plugin.get("packageManifest"), dict) else None
        return str(plugin.get("pluginId") or plugin_id or "").strip() or None, package_candidate
    if isinstance(plugin, str) and plugin.strip():
        return plugin.strip(), package_manifest
    return (str(plugin_id or "").strip() or None, package_manifest)


def renderable_profile_fields(
    plugin: dict[str, Any] | str | None = None,
    *,
    plugin_id: str | None = None,
    package_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_plugin_id, resolved_manifest = _coerce_profile_inputs(
        plugin,
        plugin_id=plugin_id,
        package_manifest=package_manifest,
    )
    profile_key = resolve_plugin_profile_key(plugin_id=resolved_plugin_id, package_manifest=resolved_manifest)
    if not profile_key:
        return {"renderMode": "config_schema", "renderableFields": []}
    profile = dict(_PROFILE_DEFS.get(profile_key) or {})
    return {
        "renderMode": str(profile.get("renderMode") or "config_schema"),
        "renderableFields": [dict(item) for item in list(profile.get("renderableFields") or []) if isinstance(item, dict)],
    }


def transport_profile(
    plugin: dict[str, Any] | str | None = None,
    *,
    plugin_id: str | None = None,
    package_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_plugin_id, resolved_manifest = _coerce_profile_inputs(
        plugin,
        plugin_id=plugin_id,
        package_manifest=package_manifest,
    )
    profile_key = resolve_plugin_profile_key(plugin_id=resolved_plugin_id, package_manifest=resolved_manifest)
    profile = dict(_PROFILE_DEFS.get(profile_key or "") or {})
    return {
        "chatTypes": list(profile.get("chatTypes") or []),
        "groupSupported": bool(profile.get("groupSupported", False)),
        "audioOutbound": str(profile.get("audioOutbound") or "none"),
        "audioInbound": str(profile.get("audioInbound") or "none"),
        "fileOutbound": str(profile.get("fileOutbound") or "none"),
        "voiceDeliveryMode": str(profile.get("voiceDeliveryMode") or "unsupported"),
    }


def support_profile(
    plugin: dict[str, Any] | str | None = None,
    *,
    plugin_id: str | None = None,
    package_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_plugin_id, resolved_manifest = _coerce_profile_inputs(
        plugin,
        plugin_id=plugin_id,
        package_manifest=package_manifest,
    )
    profile_key = resolve_plugin_profile_key(plugin_id=resolved_plugin_id, package_manifest=resolved_manifest)
    profile = dict(_PROFILE_DEFS.get(profile_key or "") or {})
    return {
        "supportTier": str(profile.get("supportTier") or "registered only"),
        "executionSupport": str(profile.get("executionSupport") or "execution unsupported"),
        "familyAdapterReady": bool(profile.get("familyAdapterReady", False)),
        "registrationMode": str(profile.get("registrationMode") or "none"),
    }


def callable_tool_defs(
    plugin: dict[str, Any] | str | None = None,
    *,
    plugin_id: str | None = None,
    package_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_plugin_id, resolved_manifest = _coerce_profile_inputs(
        plugin,
        plugin_id=plugin_id,
        package_manifest=package_manifest,
    )
    profile_key = resolve_plugin_profile_key(plugin_id=resolved_plugin_id, package_manifest=resolved_manifest)
    profile = dict(_PROFILE_DEFS.get(profile_key or "") or {})
    return {
        "registrationMode": str(profile.get("registrationMode") or "none"),
        "callableTools": [dict(item) for item in list(profile.get("callableTools") or []) if isinstance(item, dict)],
    }


def onboarding_profile(
    plugin: dict[str, Any] | str | None = None,
    *,
    plugin_id: str | None = None,
    package_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_plugin_id, resolved_manifest = _coerce_profile_inputs(
        plugin,
        plugin_id=plugin_id,
        package_manifest=package_manifest,
    )
    profile_key = resolve_plugin_profile_key(plugin_id=resolved_plugin_id, package_manifest=resolved_manifest)
    profile = dict(_PROFILE_DEFS.get(profile_key or "") or {})
    return {
        "onboardingType": str(profile.get("onboardingType") or "config_only"),
        "actionMode": str(profile.get("actionMode") or "config_form"),
        "manualSteps": [str(item).strip() for item in list(profile.get("manualSteps") or []) if str(item).strip()],
        "docsUrl": str(profile.get("docsUrl") or "").strip() or None,
        "requiredSecrets": [str(item).strip() for item in list(profile.get("requiredSecrets") or []) if str(item).strip()],
        "requiredIds": [str(item).strip() for item in list(profile.get("requiredIds") or []) if str(item).strip()],
        "pairingMode": str(profile.get("pairingMode") or "none"),
    }


def builtin_install_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in _BUILTIN_INSTALL_CATALOG]
