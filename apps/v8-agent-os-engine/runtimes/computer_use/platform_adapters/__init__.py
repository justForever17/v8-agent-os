from .factory import create_platform_discovery_providers
from .linux_apps import LinuxAppDiscoveryProvider
from .mac_apps import MacAppDiscoveryProvider
from .windows_apps import WindowsAppDiscoveryProvider

__all__ = [
    "WindowsAppDiscoveryProvider",
    "MacAppDiscoveryProvider",
    "LinuxAppDiscoveryProvider",
    "create_platform_discovery_providers",
]
