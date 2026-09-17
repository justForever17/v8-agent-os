"""One deterministic device action inside the existing episode queue/lease."""
from __future__ import annotations

import asyncio

from .protocol import TERMINAL
from .service import get_executor_service


async def execute_device_episode(episode: dict) -> dict:
    from core.runtime_episodes import build_handoff_ref
    service = get_executor_service()
    inputs = episode["inputs"]
    owner, command = inputs["ownerId"], inputs["commandId"]
    try:
        service.activate(owner, command)
        while True:
            result = service.status(owner, command)
            if result["status"] in TERMINAL:
                break
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        # The receipt owner preserves cancel-vs-complete races. The task stops
        # waiting, while Network still receives settle/unknown receipts.
        service.cancel(owner, command)
        raise
    return build_handoff_ref(producer_episode_id=episode["episodeId"], kind="device_action",
        status="ready" if result["status"] == "succeeded" else "blocked",
        compact_summary=f"Device driver: {result['status']}; business result remains unverified.",
        detail_tool="device_broker", raw_ref=f"executor-command:{command}",
        consumer_hint="Read command status and its post-action observation. Do not repeat unknown effects.",
        extra={"commandId": command, "deviceId": result["deviceId"], "driverStatus": result["status"],
               "businessVerification": "unverified", "cancelRequested": result["cancelRequested"], "recoverable": False})
