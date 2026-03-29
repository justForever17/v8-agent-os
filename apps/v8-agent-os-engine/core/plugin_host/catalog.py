from __future__ import annotations

from typing import Any

from .profiles import builtin_install_catalog


def build_install_catalog() -> list[dict[str, Any]]:
    return builtin_install_catalog()
