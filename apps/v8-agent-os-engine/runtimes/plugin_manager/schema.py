from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ComponentType = Literal["cli", "skill", "mcp", "ui_adapter", "provider_adapter"]
ConfigRequirementKind = Literal["secret", "text", "url", "enum", "boolean", "oauth", "cli_login", "file"]
ConfigRequirementSource = Literal["manifest", "mcp_schema", "cli_adapter", "hint"]
ConfigRequirementConfidence = Literal["authoritative", "reviewed", "hint"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OfficialLinks(StrictModel):
    homepage: str
    documentation: str
    repository: str | None = None
    support: str | None = None


class BrandAsset(StrictModel):
    file: str
    source: str
    license: str
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("brand asset sha256 must be a 64-character lowercase hex digest")
        return normalized


class CommandSpec(StrictModel):
    argv: list[str] = Field(min_length=1)
    cwd: str | None = None
    timeoutSeconds: int = Field(default=120, ge=1, le=3600)
    requiresElevation: bool = False
    mayRestart: bool = False
    estimatedDownloadMb: int | None = Field(default=None, ge=0)
    downloadUrl: str | None = None
    downloadTarget: str | None = None
    downloadSha256: str | None = None
    archiveFormat: Literal["zip"] | None = None
    archiveEntry: str | None = None

    @model_validator(mode="after")
    def validate_managed_download(self) -> "CommandSpec":
        fields = (self.downloadUrl, self.downloadTarget, self.downloadSha256)
        if any(fields) and not all(fields):
            raise ValueError("managed downloads require URL, target and SHA-256")
        if self.downloadSha256:
            digest = self.downloadSha256.strip().lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("download SHA-256 must be lowercase hexadecimal")
            self.downloadSha256 = digest
        archive_fields = (self.archiveFormat, self.archiveEntry)
        if any(archive_fields) and not all(archive_fields):
            raise ValueError("managed archive downloads require format and exact entry")
        if self.archiveFormat and not all(fields):
            raise ValueError("managed archive extraction requires a verified download contract")
        if self.archiveEntry:
            archive_entry = PurePosixPath(str(self.archiveEntry).replace("\\", "/"))
            if archive_entry.is_absolute() or ".." in archive_entry.parts or not archive_entry.parts:
                raise ValueError("managed archive entry must be a safe relative path")
            self.archiveEntry = archive_entry.as_posix()
        return self


class PluginConfigRequirement(StrictModel):
    id: str
    kind: ConfigRequirementKind
    required: bool = True
    source: ConfigRequirementSource = "manifest"
    confidence: ConfigRequirementConfidence = "authoritative"
    labelKey: str
    helpKey: str | None = None
    options: list[str] = Field(default_factory=list)
    target: Literal["env", "header", "url", "arg", "oauth", "file", "config"] = "config"
    targetName: str | None = None
    componentId: str | None = None
    importSources: list[str] = Field(default_factory=list)


class CliActionParameter(StrictModel):
    name: str
    sourceName: str | None = None
    kind: Literal["text", "enum", "boolean", "file", "integer", "number", "json"] = "text"
    required: bool = False
    flag: str | None = None
    positional: bool = False
    options: list[str] = Field(default_factory=list)
    description: str | None = None
    defaultValue: Any = None


class CliAction(StrictModel):
    id: str
    argv: list[str] = Field(default_factory=list)
    parameters: list[CliActionParameter] = Field(default_factory=list)
    timeoutSeconds: int = Field(default=600, ge=1, le=3600)
    mutating: bool = False
    description: str | None = None
    source: Literal["manifest", "discovered_schema"] = "manifest"
    inputSchema: dict[str, Any] = Field(default_factory=dict)
    outputSchema: dict[str, Any] = Field(default_factory=dict)
    deprecated: bool = False
    replacementActionId: str | None = None


class CliCapabilitySync(StrictModel):
    adapter: Literal["mediakit_cli_v1"]
    snapshotPath: str = "capabilities/cli-schema.json"
    blockBreakingUpgrade: bool = True

    @field_validator("snapshotPath")
    @classmethod
    def validate_snapshot_path(cls, value: str) -> str:
        normalized = PurePosixPath(str(value or "").replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
            raise ValueError("CLI capability snapshot path must be a safe relative path")
        return normalized.as_posix()


class CliProfile(StrictModel):
    id: str
    commands: list[str] = Field(min_length=1)
    platforms: list[Literal["windows", "macos", "linux"]] = Field(min_length=1)
    architectures: list[Literal["amd64", "arm64"]] = Field(default_factory=list)
    ownership: Literal["managed", "external"] = "managed"
    install: CommandSpec
    detect: CommandSpec
    version: CommandSpec
    login: CommandSpec | None = None
    start: CommandSpec | None = None
    stop: CommandSpec | None = None
    uninstall: CommandSpec | None = None
    shimCommand: list[str] = Field(default_factory=list)
    allowedArguments: list[str] = Field(default_factory=list)
    actions: list[CliAction] = Field(default_factory=list)
    capabilitySync: CliCapabilitySync | None = None
    configRequirements: list[PluginConfigRequirement] = Field(default_factory=list)
    outputProtocol: Literal["text", "json", "ndjson"] = "text"
    environment: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_install_contract(self) -> "CliProfile":
        if self.ownership == "external":
            executable = self.install.argv[0].lower()
            if executable not in {"winget", "winget.exe"}:
                raise ValueError("external CLI installation must use the reviewed winget adapter")
            if "--id" not in self.install.argv or "--exact" not in self.install.argv:
                raise ValueError("external winget installation requires an exact package id")
        action_ids = [item.id for item in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("CLI action ids must be unique within a profile")
        for name, value in self.environment.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", str(name)):
                raise ValueError("CLI environment names must use uppercase ASCII identifiers")
            if not isinstance(value, str) or len(value) > 1024:
                raise ValueError("CLI environment values must be short strings")
        return self


class SkillComponent(StrictModel):
    id: str
    sourceKind: Literal["git", "managed_cli"] = "git"
    sourceComponentId: str | None = None
    repository: str
    path: str
    revision: str
    officialOrganization: str
    targetDirectory: str
    skillNames: list[str] = Field(default_factory=list)
    sourceTrust: Literal["official", "reviewed_community"] = "official"
    sourceLicense: str | None = None
    reviewNote: str | None = None

    @model_validator(mode="after")
    def validate_source_contract(self) -> "SkillComponent":
        source_text = str(self.path or "").replace("\\", "/").strip()
        source_path = PurePosixPath(source_text)
        if (
            not source_text
            or source_path.is_absolute()
            or ".." in source_path.parts
            or (not source_path.parts and source_text != ".")
        ):
            raise ValueError("skill path must be a safe relative path")
        target_path = PurePosixPath(str(self.targetDirectory or "").replace("\\", "/"))
        if target_path.is_absolute() or ".." in target_path.parts or len(target_path.parts) != 1:
            raise ValueError("skill targetDirectory must be one safe directory name")
        if self.sourceKind == "managed_cli" and not str(self.sourceComponentId or "").strip():
            raise ValueError("managed CLI skill requires sourceComponentId")
        if self.sourceKind == "git" and self.sourceComponentId:
            raise ValueError("git skill must not declare sourceComponentId")
        normalized_names = [str(item or "").strip() for item in self.skillNames]
        if any(not item for item in normalized_names) or len(normalized_names) != len(set(normalized_names)):
            raise ValueError("skillNames must contain unique non-empty names")
        if self.sourceTrust == "reviewed_community":
            if not re.fullmatch(r"[0-9a-f]{40}", str(self.revision or "").lower()):
                raise ValueError("reviewed community Skill revisions must be pinned full commit SHAs")
            if not str(self.sourceLicense or "").strip() or not str(self.reviewNote or "").strip():
                raise ValueError("reviewed community Skills require license and review note provenance")
        self.skillNames = normalized_names
        return self


class McpComponent(StrictModel):
    id: str
    serverName: str
    repository: str | None = None
    url: str | None = None
    transport: Literal["stdio", "http", "sse"]
    configTemplate: dict[str, Any]
    authFields: list[str] = Field(default_factory=list)
    configRequirements: list[PluginConfigRequirement] = Field(default_factory=list)
    allowedTools: list[str] = Field(default_factory=list)
    officialOrganization: str

    @model_validator(mode="after")
    def require_source(self) -> "McpComponent":
        if not self.repository and not self.url:
            raise ValueError("MCP component requires repository or url")
        normalized_tools = [str(item or "").strip() for item in self.allowedTools]
        if any(not item for item in normalized_tools):
            raise ValueError("MCP allowedTools entries must be non-empty")
        if "*" in normalized_tools:
            raise ValueError("MCP allowedTools must enumerate exact reviewed tool names; wildcard is forbidden")
        if len(normalized_tools) != len(set(normalized_tools)):
            raise ValueError("MCP allowedTools entries must be unique")
        self.allowedTools = normalized_tools
        return self


class PresentationPolicy(StrictModel):
    web: Literal["inline", "edge_to_edge"] = "inline"
    phone: Literal["inline", "modal"] = "inline"


class UiAdapterComponent(StrictModel):
    id: str
    renderer: str
    resourcePrefix: str
    allowedFrameOrigins: list[str]
    presentation: PresentationPolicy


class ProviderAdapterComponent(StrictModel):
    id: str
    handlerId: str
    configRequirements: list[PluginConfigRequirement] = Field(default_factory=list)


class Governance(StrictModel):
    permissions: list[str] = Field(default_factory=list)
    sideEffects: list[str] = Field(default_factory=list)
    paidOperations: bool = False
    networkRequired: bool = True
    workspaceAccess: Literal["none", "read", "write"] = "none"
    approvalClasses: list[str] = Field(default_factory=list)
    healthChecks: list[str] = Field(default_factory=list)
    outputProtocols: list[str] = Field(default_factory=lambda: ["text"])


class PluginManifest(StrictModel):
    schemaVersion: Literal["v8.plugin.v1"]
    id: str
    displayName: str
    version: str
    publisher: str
    category: str
    description: str
    officialDomains: list[str] = Field(min_length=1)
    officialOrganizations: list[str] = Field(default_factory=list)
    reviewedOrganizations: list[str] = Field(default_factory=list)
    officialLinks: OfficialLinks
    brand: BrandAsset
    cliProfiles: list[CliProfile] = Field(default_factory=list)
    skills: list[SkillComponent] = Field(default_factory=list)
    mcpServers: list[McpComponent] = Field(default_factory=list)
    uiAdapters: list[UiAdapterComponent] = Field(default_factory=list)
    providerAdapters: list[ProviderAdapterComponent] = Field(default_factory=list)
    governance: Governance
    capabilities: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in normalized):
            raise ValueError("plugin id must use lowercase ASCII letters, digits, hyphen or underscore")
        return normalized

    @model_validator(mode="after")
    def verify_official_components(self) -> "PluginManifest":
        domains = {item.lower().lstrip(".") for item in self.officialDomains}
        organizations = {item.lower() for item in self.officialOrganizations}
        reviewed_organizations = {item.lower() for item in self.reviewedOrganizations}
        cli_profiles = {item.id: item for item in self.cliProfiles}

        def official_url(value: str) -> bool:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower()
            if host == "github.com":
                path_parts = [part for part in parsed.path.split("/") if part]
                return bool(path_parts and path_parts[0].lower() in organizations)
            return any(host == domain or host.endswith(f".{domain}") for domain in domains)

        for skill in self.skills:
            if skill.sourceTrust == "official":
                if skill.officialOrganization.lower() not in organizations or not official_url(skill.repository):
                    raise ValueError(f"skill {skill.id} is not proven to come from an official organization")
            else:
                parsed = urlparse(skill.repository)
                path_parts = [part for part in parsed.path.split("/") if part]
                source_organization = skill.officialOrganization.lower()
                if (
                    (parsed.hostname or "").lower() != "github.com"
                    or len(path_parts) < 2
                    or path_parts[0].lower() != source_organization
                    or source_organization not in reviewed_organizations
                ):
                    raise ValueError(f"skill {skill.id} is not declared as a reviewed community source")
            if skill.sourceKind == "managed_cli":
                source_profile = cli_profiles.get(str(skill.sourceComponentId or ""))
                if source_profile is None or source_profile.ownership != "managed":
                    raise ValueError(f"skill {skill.id} must reference a managed CLI component")
        for mcp in self.mcpServers:
            if mcp.officialOrganization.lower() not in organizations:
                raise ValueError(f"MCP {mcp.id} official organization is not declared")
            for source in (mcp.repository, mcp.url):
                if source and not official_url(source):
                    raise ValueError(f"MCP {mcp.id} source is not official: {source}")
        for profile in self.cliProfiles:
            for command in (profile.install, profile.detect, profile.version, profile.login, profile.start, profile.stop, profile.uninstall):
                if command and command.downloadUrl and not official_url(command.downloadUrl):
                    raise ValueError(f"CLI {profile.id} download source is not official: {command.downloadUrl}")
        component_ids = [
            *[item.id for item in self.cliProfiles],
            *[item.id for item in self.skills],
            *[item.id for item in self.mcpServers],
            *[item.id for item in self.uiAdapters],
            *[item.id for item in self.providerAdapters],
        ]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("plugin component ids must be unique across component types")
        for adapter in self.providerAdapters:
            if not re.fullmatch(r"[a-z0-9_.-]+", adapter.handlerId):
                raise ValueError(f"provider adapter {adapter.id} handlerId is invalid")
        return self


class PluginCatalog(StrictModel):
    schemaVersion: Literal["v8.plugin.catalog.v1"]
    revision: int = Field(ge=1)
    sequence: int | None = Field(default=None, ge=1)
    keyId: str | None = None
    generatedAt: str
    expiresAt: str | None = None
    revocations: list[str] = Field(default_factory=list)
    plugins: list[PluginManifest] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_plugins(self) -> "PluginCatalog":
        ids = [item.id for item in self.plugins]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog contains duplicate plugin ids")
        return self
