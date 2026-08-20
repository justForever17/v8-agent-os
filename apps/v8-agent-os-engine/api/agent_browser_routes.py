from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from core.storage import storage
from runtimes.computer_use.browser_automation import agent_browser_automation

from .models import ComputerUseAgentBrowserOpenPayload


router = APIRouter()


def _configured_agent_browser_provider():
    # Manual Agent Browser profile setup is a Research product surface.  It is
    # intentionally independent of the optional Computer Use feature pack,
    # while reusing that runtime's governed browser provider when installed.
    agent_browser_automation.configure(dict(storage.get_computer_use_config() or {}))
    return agent_browser_automation


def open_agent_browser_profile(*, url: str = "about:blank") -> dict[str, Any]:
    return dict(
        _configured_agent_browser_provider().open_agent_browser(
            browser_kind="auto",
            url=str(url or "about:blank"),
        )
        or {}
    )


@router.post("/agent-browser/open")
async def open_agent_browser(payload: ComputerUseAgentBrowserOpenPayload):
    # Cold browser/CDP startup can legitimately take several seconds.  Keep it
    # off the Engine event loop so chat/realtime and Admin health remain live.
    return await asyncio.to_thread(
        open_agent_browser_profile,
        url=payload.url or "about:blank",
    )
