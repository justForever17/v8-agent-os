from __future__ import annotations

from dataclasses import dataclass
import webbrowser

from .schema import CliProfile, PluginManifest


@dataclass(frozen=True, slots=True)
class CliBrowserAuthAdapter:
    """Reviewed, non-interactive browser login contract for an official CLI."""

    plugin_id: str
    profile_id: str
    login_suffix: tuple[str, ...]
    status_suffix: tuple[str, ...]
    browser_url: str
    clear_environment: tuple[str, ...] = ()

    def login_argv(self, profile: CliProfile) -> list[str]:
        if profile.login is None:
            return []
        return [*profile.login.argv, *self.login_suffix]

    def status_argv(self, profile: CliProfile) -> list[str]:
        return [profile.commands[0], *self.status_suffix]


_BROWSER_AUTH_ADAPTERS = {
    ("github", "gh"): CliBrowserAuthAdapter(
        plugin_id="github",
        profile_id="gh",
        login_suffix=(
            "--web",
            "--clipboard",
            "--hostname",
            "github.com",
            "--git-protocol",
            "https",
        ),
        status_suffix=("auth", "status", "--hostname", "github.com"),
        browser_url="https://github.com/login/device",
        # Explicit login must inspect the credential store instead of inheriting
        # a temporary automation token from the Engine process.
        clear_environment=("GH_TOKEN", "GITHUB_TOKEN"),
    ),
}


def open_system_browser(url: str) -> bool:
    """Open one reviewed adapter URL with the operating system's default browser."""

    normalized = str(url or "").strip()
    allowed_urls = {adapter.browser_url for adapter in _BROWSER_AUTH_ADAPTERS.values()}
    if normalized not in allowed_urls:
        return False
    try:
        return bool(webbrowser.open(normalized, new=2, autoraise=True))
    except (OSError, webbrowser.Error):
        return False


def browser_auth_adapter(
    manifest: PluginManifest,
    profile: CliProfile,
) -> CliBrowserAuthAdapter | None:
    return _BROWSER_AUTH_ADAPTERS.get((manifest.id, profile.id))
