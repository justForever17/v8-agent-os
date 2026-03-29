from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")


def _normalize_plugin_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "gateway"


def _normalize_tool_name(value: str | None) -> str:
    return str(value or "").strip()


def _tool_name_parts(tool_name: str) -> list[str]:
    return [part for part in _normalize_tool_name(tool_name).split("_") if part]


def _family_prefix_for_tool(*, tool_name: str, sibling_tool_names: Iterable[str]) -> str | None:
    parts = _tool_name_parts(tool_name)
    if len(parts) < 2:
        return None
    normalized_siblings = [_normalize_tool_name(item) for item in sibling_tool_names if _normalize_tool_name(item)]
    if len(normalized_siblings) < 2:
        return None
    for width in range(len(parts) - 1, 0, -1):
        prefix = "_".join(parts[:width])
        matches = [item for item in normalized_siblings if item == prefix or item.startswith(f"{prefix}_")]
        if len(matches) >= 2:
            return prefix
    return None


def expand_tool_family_seeds(
    *,
    items: Iterable[T],
    seeds: Iterable[T],
    get_plugin_id: Callable[[T], str | None],
    get_tool_name: Callable[[T], str | None],
    get_identity: Callable[[T], str],
    get_sort_key: Callable[[T], Any] | None = None,
    max_items: int | None = None,
) -> list[T]:
    items_list = list(items)
    seeds_list = list(seeds)
    if not items_list or not seeds_list:
        return seeds_list[: max_items] if max_items else seeds_list

    grouped: dict[str, list[tuple[str, T]]] = {}
    for item in items_list:
        plugin_id = _normalize_plugin_id(get_plugin_id(item))
        tool_name = _normalize_tool_name(get_tool_name(item))
        if not tool_name:
            continue
        grouped.setdefault(plugin_id, []).append((tool_name, item))

    def _default_sort_key(item: T) -> tuple[str, str]:
        return (
            _normalize_plugin_id(get_plugin_id(item)).lower(),
            _normalize_tool_name(get_tool_name(item)).lower(),
        )

    sort_key = get_sort_key or _default_sort_key
    expanded: list[T] = []
    seen: set[str] = set()

    def _append(candidate: T) -> None:
        identity = str(get_identity(candidate) or "").strip()
        if not identity or identity in seen:
            return
        seen.add(identity)
        expanded.append(candidate)

    for seed in seeds_list:
        _append(seed)
        plugin_id = _normalize_plugin_id(get_plugin_id(seed))
        tool_name = _normalize_tool_name(get_tool_name(seed))
        sibling_entries = grouped.get(plugin_id) or []
        family_prefix = _family_prefix_for_tool(
            tool_name=tool_name,
            sibling_tool_names=[name for name, _item in sibling_entries],
        )
        if not family_prefix:
            if max_items and len(expanded) >= max_items:
                break
            continue
        family_items = [
            item
            for name, item in sibling_entries
            if name == family_prefix or name.startswith(f"{family_prefix}_")
        ]
        for sibling in sorted(family_items, key=sort_key):
            _append(sibling)
            if max_items and len(expanded) >= max_items:
                break
        if max_items and len(expanded) >= max_items:
            break

    if max_items:
        return expanded[:max_items]
    return expanded
