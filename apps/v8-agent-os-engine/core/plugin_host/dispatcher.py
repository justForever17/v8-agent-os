from __future__ import annotations

from typing import Optional, Tuple

from runtimes.plugin_host.runtime import PluginHostMessage as RuntimePluginHostMessage, plugin_host_runtime

PluginHostMessage = RuntimePluginHostMessage


class PluginHostDispatcher:
    """
    PluginHostRuntime 的薄转发壳。

    外部 SDK 若仍依赖旧入口，也只应把调用转发到 PluginHostRuntime。
    """

    @classmethod
    def resolve_session_id(cls, source: str, remote_id: str, chat_type: str) -> str:
        return plugin_host_runtime.resolve_session_id(source, remote_id, chat_type)

    @classmethod
    async def dispatch_message(
        cls,
        source: str,
        chat_type: str,
        remote_id: str,
        message: PluginHostMessage,
        audio_trigger: bool = False,
        record_only: bool = False,
    ) -> Tuple[str, Optional[str], str]:
        return await plugin_host_runtime.dispatch_message(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            message=message,
            audio_trigger=audio_trigger,
            record_only=record_only,
        )

    @classmethod
    async def _generate_tts(cls, final_response: str) -> dict | None:
        return await plugin_host_runtime._generate_tts(final_response)
