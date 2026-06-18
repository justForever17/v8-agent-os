"""Lightweight ERC package exports.

Keep this module lazy: core modules such as ``core.runtime_episodes`` import
``erc.runtime_context`` during initialization, and eager package-level imports
can re-enter ``erc.command_router`` before runtime episode constants exist.
"""

from __future__ import annotations

from typing import Any

__all__ = ["erc_kernel", "runtime_command_router", "runtime_registry", "capability_registry"]


def __getattr__(name: str) -> Any:
    if name == "erc_kernel":
        from erc.kernel import erc_kernel

        return erc_kernel
    if name == "runtime_command_router":
        from erc.command_router import runtime_command_router

        return runtime_command_router
    if name == "runtime_registry":
        from erc.runtime_registry import runtime_registry

        return runtime_registry
    if name == "capability_registry":
        from erc.capability_registry import capability_registry

        return capability_registry
    raise AttributeError(name)
