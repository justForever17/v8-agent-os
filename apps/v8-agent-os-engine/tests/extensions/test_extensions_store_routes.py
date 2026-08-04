from __future__ import annotations

import asyncio

from api import extensions_routes


def test_extensions_store_routes_offload_blocking_store_work(monkeypatch) -> None:
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def fake_to_thread(function, /, *args, **kwargs):
        calls.append((function, args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(extensions_routes.asyncio, "to_thread", fake_to_thread)

    async def exercise_routes() -> None:
        await extensions_routes.get_extensions_store_skills(query="image", limit=12, refresh=True)
        await extensions_routes.get_extensions_store_skill_detail(
            source="owner/repository",
            skillId="image-tools",
            refresh=True,
        )
        await extensions_routes.get_extensions_store_mcp(query="files", limit=8, refresh=True)
        await extensions_routes.get_extensions_store_mcp_detail(id="owner/server", refresh=True)
        await extensions_routes.install_extensions_store_skill({"source": "owner/repository", "skillId": "image-tools"})
        await extensions_routes.install_extensions_store_mcp({"id": "owner/server"})

    asyncio.run(exercise_routes())

    assert calls == [
        (extensions_routes.list_store_skills, (), {"query": "image", "limit": 12, "refresh": True}),
        (
            extensions_routes.get_store_skill_detail,
            (),
            {"source": "owner/repository", "skill_id": "image-tools", "refresh": True},
        ),
        (extensions_routes.list_store_mcp, (), {"query": "files", "limit": 8, "refresh": True}),
        (extensions_routes.get_store_mcp_detail, (), {"mcp_id": "owner/server", "refresh": True}),
        (
            extensions_routes.install_store_skill,
            ({"source": "owner/repository", "skillId": "image-tools"},),
            {},
        ),
        (extensions_routes.install_store_mcp, ({"id": "owner/server"},), {}),
    ]
