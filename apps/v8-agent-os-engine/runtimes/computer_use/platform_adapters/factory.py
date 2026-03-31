from __future__ import annotations

from typing import List

from runtimes.computer_use.app_catalog import ComputerUseAppDiscoveryProvider


def create_platform_discovery_providers(*, driver) -> List[ComputerUseAppDiscoveryProvider]:
    providers: List[ComputerUseAppDiscoveryProvider] = []
    platform = str(getattr(driver, "platform", "") or "").strip().lower()
    if platform == "windows":
        from .windows_apps import WindowsAppDiscoveryProvider

        providers.append(WindowsAppDiscoveryProvider(driver=driver))
    elif platform == "macos":
        from .mac_apps import MacAppDiscoveryProvider

        providers.append(MacAppDiscoveryProvider(driver=driver))
    elif platform == "linux":
        from .linux_apps import LinuxAppDiscoveryProvider

        providers.append(LinuxAppDiscoveryProvider(driver=driver))
    return providers
