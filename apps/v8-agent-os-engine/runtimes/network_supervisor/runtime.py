from __future__ import annotations

from typing import Any

from erc.runtime_registry import runtime_registry


class NetworkSupervisorRuntime:
    kind = "network_supervisor"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "NetworkSupervisorRuntime",
            "summary": "负责 V8 节点之间的发现、信任、定向唤醒和显式远程任务委派。",
            "responsibilities": [
                "peer discovery / joining",
                "trust / challenge",
                "directed wake",
                "remote delegation / result return",
            ],
            "routingKeywords": ["远程委派", "跨节点协作", "局域网发现", "定向唤醒", "network supervisor"],
            "acceptedInputs": ["peer_id + task", "peer protocol envelopes", "network diagnostics"],
            "producedOutputs": ["runtime_events", "workflow_steps", "delegation results", "peer status"],
            "ownedSteps": [
                "network.wait_remote",
                "network.receive",
                "network.execute_local",
                "network.return_result",
            ],
            "supportsPause": False,
            "supportsResume": True,
            "supportsApproval": False,
            "supportsRepair": True,
            "visibility": "secondary",
            "promptHints": [
                "只有在需要跨节点协作时才显式委派到 network supervisor。",
                "远程委派是显式能力，不是自动路由。",
            ],
            "metadata": {
                "managedToolNames": ["delegate_network_task"],
            },
            "capabilities": [
                {
                    "key": "network.delegate",
                    "label": "显式远程委派",
                    "summary": "把任务发送给受信任的远端 V8 节点，并持续接收进度与结果。",
                    "accepts": ["peerId", "task", "scope metadata"],
                    "outputs": ["accepted", "progress", "result", "failed"],
                    "examples": ["让另一台节点执行长任务", "把局域网内资源密集任务委派给远端节点"],
                    "risk_level": "high",
                }
            ],
        }


network_supervisor_runtime = runtime_registry.register(NetworkSupervisorRuntime())

