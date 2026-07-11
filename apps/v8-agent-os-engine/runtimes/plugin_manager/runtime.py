from erc.runtime_registry import runtime_registry

from .service import plugin_manager_service

runtime_registry.register(plugin_manager_service)
