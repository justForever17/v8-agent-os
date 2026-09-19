"use client";
import { createProviderDraft } from "@/lib/model-hub/editor-drafts";
import { readJsonErrorMessage } from "@/lib/model-hub/errors";
import { type FormEvent, useState, useRef, useEffect } from "react";
import { useToast } from "@/components/ui/use-toast";
import { type AIProvider } from "@/lib/model-hub/types";
import { useT } from "@/components/providers/LocaleProvider";
import {
    getPlatformLoginPresetConfig,
    getLocalBackendPresetConfig,
    inferPlatformLoginPreset,
    type PlatformLoginPreset,
    PLATFORM_LOGIN_PRESETS,
    type LocalBackendPreset,
    LOCAL_BACKEND_PRESETS,
} from "@/lib/models/provider-admin";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { resolveAdminLabel, getAdminOptions } from "@/lib/admin-labels";
import { Button } from "@/components/ui/button";
import { PROVIDER_CHANNEL_PRESETS, createProviderChannel, MODEL_WIRE_PROTOCOLS } from "@/lib/model-hub/channels";
import { Plus, Trash2 } from "lucide-react";
import { isXiaomiAnthropicBaseUrl } from "@/lib/model-hub/catalog";

type Props = {
    target: AIProvider | null;
    onSaved: () => void | Promise<void>;
    onClose: () => void;
};

export function ProviderEditorDialog({ target: editingProvider, onSaved, onClose }: Props) {
    const t = useT();
    const { toast } = useToast();
    const [draft, onDraftChange] = useState(() => createProviderDraft(editingProvider));
    const entitySaveBusy = useRef(false);
    const [entitySaving, setEntitySaving] = useState(false);
    const [entitySaveError, setEntitySaveError] = useState("");
    const lifetime = useRef<AbortController | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        lifetime.current = controller;
        return () => controller.abort();
    }, []);

    const {
        providerType,
        providerCredentialMode,
        providerApiStandard,
        providerBaseUrl,
        providerChannels,
        providerDefaultChannelId,
        providerApiKey,
        providerOauthPath,
        platformLoginPreset,
        localBackendPreset,
    } = draft;
    const platformProviderSelected = providerType === "PLATFORM";
    const activePlatformPreset = getPlatformLoginPresetConfig(platformLoginPreset);
    const oauthHint = platformProviderSelected
        ? t(activePlatformPreset.helpText)
        : t("app.admin.dashboard.model.hub.page.k2daf728b");
    const localBackendConfig = getLocalBackendPresetConfig(localBackendPreset);
    const handleSaveProvider = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (entitySaveBusy.current) return;
        entitySaveBusy.current = true;
        const signal = lifetime.current?.signal;
        setEntitySaving(true);
        setEntitySaveError("");
        try {
            const formData = new FormData(event.currentTarget);
            const payload: Record<string, unknown> = Object.fromEntries(formData.entries());
            if (providerType === "API") {
                const normalizedChannels = providerChannels.map((channel) => ({
                    ...channel,
                    id: channel.id.trim().toLowerCase(),
                    label: channel.label.trim() || channel.id.trim(),
                    baseUrl: channel.baseUrl.trim().replace(/\/+$/, ""),
                    apiVersion: channel.apiVersion.trim().replace(/^\/+|\/+$/g, ""),
                }));
                const invalidAnthropicChannel = normalizedChannels.find(
                    (channel) => channel.apiStandard === "anthropic" && /\/v1$/i.test(channel.baseUrl),
                );
                if (invalidAnthropicChannel) {
                    toast({
                        variant: "destructive",
                        title: t("app.admin.dashboard.model.hub.page.kd2b2caac"),
                        description: t("app.admin.dashboard.model.hub.channel.anthropicBaseUrlError"),
                    });
                    return;
                }
                const defaultChannel =
                    normalizedChannels.find((channel) => channel.id === providerDefaultChannelId) ||
                    normalizedChannels[0];
                payload.channels = normalizedChannels;
                payload.defaultChannelId = defaultChannel?.id || "";
                payload.baseUrl = defaultChannel?.baseUrl || providerBaseUrl;
                payload.apiStandard = defaultChannel?.apiStandard || providerApiStandard;
            }
            const url = editingProvider ? `/api/providers/${editingProvider.id}` : "/api/providers";
            const method = editingProvider ? "PUT" : "POST";
            const response = await fetch(url, {
                method,
                headers: { "Content-Type": "application/json" },
                signal,
                body: JSON.stringify(payload),
            });
            if (signal?.aborted) return;
            if (!response.ok) {
                const errorMessage = await readJsonErrorMessage(
                    response,
                    t("app.admin.dashboard.model.hub.page.kd2b2caac"),
                );
                if (signal?.aborted) return;
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.page.kd2b2caac"),
                    description: errorMessage,
                });
                return;
            }
            await onSaved();
        } catch (error) {
            if (!signal?.aborted) setEntitySaveError(`${t("admin.experience.saveFailed")} ${String(error)}`);
        } finally {
            entitySaveBusy.current = false;
            if (!signal?.aborted) setEntitySaving(false);
        }
    };
    return (
        <Dialog
            open
            onOpenChange={(open) => {
                if (!open && !entitySaving) onClose();
            }}
        >
            <DialogContent guardUnsaved className="admin-editor-modal sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>
                        {editingProvider
                            ? t("app.admin.dashboard.model.hub.page.k03d9a3c5")
                            : t("app.admin.dashboard.model.hub.page.k9e31d9ed")}
                    </DialogTitle>
                </DialogHeader>
                <form key={editingProvider?.id || "new"} onSubmit={handleSaveProvider} className="admin-editor-form">
                    <fieldset disabled={entitySaving} className="admin-editor-body space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="provider-name">{t("app.admin.dashboard.model.hub.page.kd00c0239")}</Label>
                            <Input id="provider-name" name="name" defaultValue={editingProvider?.name || ""} required />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="provider-code">{t("app.admin.dashboard.model.hub.page.ke46386e9")}</Label>
                            <Input id="provider-code" name="code" defaultValue={editingProvider?.code || ""} required />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="provider-type">{t("app.admin.dashboard.model.hub.page.k8de6f532")}</Label>
                            <input type="hidden" name="type" value={providerType} />
                            <Select
                                value={providerType}
                                onValueChange={(value: AIProvider["type"]) => {
                                    onDraftChange((draft) => ({ ...draft, providerType: value }));
                                    if (value === "PLATFORM") {
                                        const preset = editingProvider
                                            ? inferPlatformLoginPreset({
                                                  providerType: value,
                                                  apiStandard: providerApiStandard,
                                                  baseUrl: providerBaseUrl,
                                                  oauthPath: providerOauthPath,
                                                  code: editingProvider.code,
                                                  name: editingProvider.name,
                                              })
                                            : platformLoginPreset;
                                        const config = getPlatformLoginPresetConfig(preset);
                                        onDraftChange((draft) => ({
                                            ...draft,
                                            providerCredentialMode: "oauthFile",
                                            platformLoginPreset: preset,
                                            providerApiStandard: config.apiStandard,
                                            providerBaseUrl: config.baseUrl,
                                            providerOauthPath: providerOauthPath || config.oauthPath,
                                            providerApiKey: "",
                                        }));
                                    } else if (value === "LOCAL") {
                                        const config = getLocalBackendPresetConfig(localBackendPreset);
                                        onDraftChange((draft) => ({
                                            ...draft,
                                            providerCredentialMode: "apiKey",
                                            providerApiStandard: config.apiStandard,
                                            providerBaseUrl: config.baseUrl,
                                            providerApiKey: config.apiKey,
                                            providerOauthPath: "",
                                        }));
                                    }
                                }}
                            >
                                <SelectTrigger id="provider-type">
                                    <SelectValue>{resolveAdminLabel(t, "providerType", providerType)}</SelectValue>
                                </SelectTrigger>
                                <SelectContent>
                                    {getAdminOptions("providerType").map((option) => (
                                        <SelectItem key={option.value} value={option.value}>
                                            {t(option.labelKey)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        {platformProviderSelected ? (
                            <>
                                <input type="hidden" name="platformLoginPreset" value={platformLoginPreset} />
                                <div className="space-y-2">
                                    <Label htmlFor="platform-login-preset">
                                        {t("app.admin.dashboard.model.hub.page.k1f6f2bda")}
                                    </Label>
                                    <Select
                                        value={platformLoginPreset}
                                        onValueChange={(value: PlatformLoginPreset) => {
                                            const config = getPlatformLoginPresetConfig(value);
                                            onDraftChange((draft) => ({
                                                ...draft,
                                                platformLoginPreset: value,
                                                providerCredentialMode: "oauthFile",
                                                providerApiStandard: config.apiStandard,
                                                providerBaseUrl: config.baseUrl,
                                                providerOauthPath: config.oauthPath,
                                            }));
                                        }}
                                    >
                                        <SelectTrigger id="platform-login-preset">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {Object.values(PLATFORM_LOGIN_PRESETS).map((preset) => (
                                                <SelectItem key={preset.id} value={preset.id}>
                                                    {preset.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">{activePlatformPreset.description}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="provider-api-standard-readonly">
                                        {t("app.admin.dashboard.model.hub.page.k3a701154")}
                                    </Label>
                                    <Input
                                        id="provider-api-standard-readonly"
                                        value={resolveAdminLabel(t, "providerApiStandard", providerApiStandard)}
                                        readOnly
                                    />
                                    <input type="hidden" name="apiStandard" value={providerApiStandard} />
                                </div>
                            </>
                        ) : providerType === "LOCAL" ? (
                            <>
                                <input type="hidden" name="localBackendPreset" value={localBackendPreset} />
                                <div className="space-y-2">
                                    <Label htmlFor="provider-local-preset">
                                        {t("app.admin.dashboard.model.hub.page.kd683ee7e")}
                                    </Label>
                                    <Select
                                        value={localBackendPreset}
                                        onValueChange={(value: LocalBackendPreset) => {
                                            const config = getLocalBackendPresetConfig(value);
                                            onDraftChange((draft) => ({
                                                ...draft,
                                                localBackendPreset: value,
                                                providerCredentialMode: "apiKey",
                                                providerApiStandard: config.apiStandard,
                                                providerBaseUrl: config.baseUrl,
                                                providerApiKey: config.apiKey,
                                            }));
                                        }}
                                    >
                                        <SelectTrigger id="provider-local-preset">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {Object.values(LOCAL_BACKEND_PRESETS).map((preset) => (
                                                <SelectItem key={preset.id} value={preset.id}>
                                                    {preset.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">{t(localBackendConfig.description)}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="provider-api-standard-readonly">
                                        {t("app.admin.dashboard.model.hub.page.k3a701154")}
                                    </Label>
                                    <Input
                                        id="provider-api-standard-readonly"
                                        value={t("app.admin.dashboard.model.hub.page.kdab0f774")}
                                        readOnly
                                    />
                                    <input type="hidden" name="apiStandard" value={providerApiStandard} />
                                </div>
                            </>
                        ) : (
                            <div className="space-y-3 rounded-xl border border-border/70 p-3">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <Label>{t("app.admin.dashboard.model.hub.channel.title")}</Label>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            {t("app.admin.dashboard.model.hub.channel.help")}
                                        </p>
                                    </div>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => {
                                            const used = new Set(providerChannels.map((channel) => channel.id));
                                            const preset =
                                                PROVIDER_CHANNEL_PRESETS.find((item) => !used.has(item.id)) ||
                                                PROVIDER_CHANNEL_PRESETS[0];
                                            const suffix =
                                                providerChannels.filter((channel) => channel.id.startsWith(preset.id))
                                                    .length + 1;
                                            onDraftChange((draft) => ({
                                                ...draft,
                                                providerChannels: [
                                                    ...draft.providerChannels,
                                                    createProviderChannel(
                                                        preset.apiStandard,
                                                        "",
                                                        used.has(preset.id) ? `${preset.id}-${suffix}` : preset.id,
                                                    ),
                                                ],
                                            }));
                                        }}
                                    >
                                        <Plus className="mr-1 h-3.5 w-3.5" />
                                        {t("app.admin.dashboard.model.hub.channel.add")}
                                    </Button>
                                </div>
                                {providerChannels.map((channel, index) => (
                                    <div
                                        key={`${channel.id}-${index}`}
                                        className="grid gap-2 rounded-lg border border-border/60 bg-muted/10 p-2 md:grid-cols-2"
                                    >
                                        <div className="flex items-center gap-2 md:col-span-2">
                                            <input
                                                type="radio"
                                                name="default-provider-channel"
                                                checked={providerDefaultChannelId === channel.id}
                                                onChange={() =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        providerDefaultChannelId: channel.id,
                                                    }))
                                                }
                                                aria-label={t("app.admin.dashboard.model.hub.channel.default")}
                                            />
                                            <span className="text-xs text-muted-foreground">
                                                {t("app.admin.dashboard.model.hub.channel.default")}
                                            </span>
                                            {providerChannels.length > 1 ? (
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="sm"
                                                    className="ml-auto h-7"
                                                    onClick={() => {
                                                        const next = providerChannels.filter(
                                                            (_, itemIndex) => itemIndex !== index,
                                                        );
                                                        onDraftChange((draft) => ({
                                                            ...draft,
                                                            providerChannels: next,
                                                        }));
                                                        if (providerDefaultChannelId === channel.id)
                                                            onDraftChange((draft) => ({
                                                                ...draft,
                                                                providerDefaultChannelId: next[0]?.id || "",
                                                            }));
                                                    }}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5" />
                                                </Button>
                                            ) : null}
                                        </div>
                                        <Input
                                            value={channel.id}
                                            onChange={(event) => {
                                                const previousId = channel.id;
                                                const nextId = event.target.value
                                                    .toLowerCase()
                                                    .replace(/[^a-z0-9._-]/g, "");
                                                onDraftChange((draft) => ({
                                                    ...draft,
                                                    providerChannels: draft.providerChannels.map((item, itemIndex) =>
                                                        itemIndex === index ? { ...item, id: nextId } : item,
                                                    ),
                                                }));
                                                if (providerDefaultChannelId === previousId)
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        providerDefaultChannelId: nextId,
                                                    }));
                                            }}
                                            placeholder="openai"
                                            aria-label={t("app.admin.dashboard.model.hub.channel.id")}
                                        />
                                        <Input
                                            value={channel.label}
                                            onChange={(event) =>
                                                onDraftChange((draft) => ({
                                                    ...draft,
                                                    providerChannels: draft.providerChannels.map((item, itemIndex) =>
                                                        itemIndex === index
                                                            ? { ...item, label: event.target.value }
                                                            : item,
                                                    ),
                                                }))
                                            }
                                            placeholder={t("app.admin.dashboard.model.hub.channel.label")}
                                        />
                                        <select
                                            value={channel.apiStandard}
                                            onChange={(event) => {
                                                const preset =
                                                    PROVIDER_CHANNEL_PRESETS.find(
                                                        (item) => item.apiStandard === event.target.value,
                                                    ) || PROVIDER_CHANNEL_PRESETS[0];
                                                onDraftChange((draft) => ({
                                                    ...draft,
                                                    providerChannels: draft.providerChannels.map((item, itemIndex) =>
                                                        itemIndex === index
                                                            ? {
                                                                  ...item,
                                                                  apiStandard: preset.apiStandard,
                                                                  wireProtocols: [...preset.wireProtocols],
                                                                  defaultWireProtocol: preset.defaultWireProtocol,
                                                              }
                                                            : item,
                                                    ),
                                                }));
                                            }}
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground"
                                        >
                                            {PROVIDER_CHANNEL_PRESETS.map((preset) => (
                                                <option key={preset.id} value={preset.apiStandard}>
                                                    {t(preset.labelKey)}
                                                </option>
                                            ))}
                                        </select>
                                        <select
                                            value={channel.defaultWireProtocol}
                                            disabled={channel.wireProtocols.length === 0}
                                            onChange={(event) =>
                                                onDraftChange((draft) => ({
                                                    ...draft,
                                                    providerChannels: draft.providerChannels.map((item, itemIndex) =>
                                                        itemIndex === index
                                                            ? { ...item, defaultWireProtocol: event.target.value }
                                                            : item,
                                                    ),
                                                }))
                                            }
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground disabled:opacity-60"
                                        >
                                            {channel.wireProtocols.length === 0 ? (
                                                <option value="">
                                                    {t("app.admin.dashboard.model.hub.channel.noWireProtocol")}
                                                </option>
                                            ) : null}
                                            {MODEL_WIRE_PROTOCOLS.filter((protocol) =>
                                                channel.wireProtocols.includes(protocol.id),
                                            ).map((protocol) => (
                                                <option key={protocol.id} value={protocol.id}>
                                                    {t(protocol.labelKey)}
                                                </option>
                                            ))}
                                        </select>
                                        <Input
                                            className="md:col-span-2"
                                            value={channel.baseUrl}
                                            onChange={(event) =>
                                                onDraftChange((draft) => ({
                                                    ...draft,
                                                    providerChannels: draft.providerChannels.map((item, itemIndex) =>
                                                        itemIndex === index
                                                            ? { ...item, baseUrl: event.target.value }
                                                            : item,
                                                    ),
                                                }))
                                            }
                                            placeholder={t(
                                                "app.admin.dashboard.model.hub.catalog.customBaseUrlPlaceholder",
                                            )}
                                        />
                                        {channel.apiStandard === "anthropic" ? (
                                            <p
                                                className={`md:col-span-2 text-xs ${/\/v1\/?$/i.test(channel.baseUrl.trim()) ? "text-destructive" : "text-muted-foreground"}`}
                                            >
                                                {t("app.admin.dashboard.model.hub.channel.anthropicBaseUrlHelp")}
                                            </p>
                                        ) : null}
                                        {channel.apiStandard === "gemini" ? (
                                            <Input
                                                className="md:col-span-2"
                                                value={channel.apiVersion}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        providerChannels: draft.providerChannels.map(
                                                            (item, itemIndex) =>
                                                                itemIndex === index
                                                                    ? { ...item, apiVersion: event.target.value }
                                                                    : item,
                                                        ),
                                                    }))
                                                }
                                                placeholder={t(
                                                    "app.admin.dashboard.model.hub.channel.apiVersionPlaceholder",
                                                )}
                                            />
                                        ) : null}
                                    </div>
                                ))}
                            </div>
                        )}
                        {providerType !== "API" ? (
                            <div className="space-y-2">
                                <Label htmlFor="provider-base-url">
                                    {t("app.admin.dashboard.model.hub.page.k8331921c")}
                                </Label>
                                <Input
                                    id="provider-base-url"
                                    name="baseUrl"
                                    value={providerBaseUrl}
                                    onChange={(event) => {
                                        const nextValue = event.target.value;
                                        onDraftChange((draft) => ({ ...draft, providerBaseUrl: nextValue }));
                                        if (
                                            isXiaomiAnthropicBaseUrl(nextValue) &&
                                            providerApiStandard !== "anthropic"
                                        ) {
                                            onDraftChange((draft) => ({ ...draft, providerApiStandard: "anthropic" }));
                                        }
                                    }}
                                />
                                {isXiaomiAnthropicBaseUrl(providerBaseUrl) ? (
                                    <p className="text-xs text-amber-600">
                                        {t("app.admin.dashboard.model.hub.catalog.manualAnthropicBaseUrlHint")}
                                    </p>
                                ) : null}
                            </div>
                        ) : null}
                        {!platformProviderSelected ? (
                            <div className="space-y-2">
                                <Label htmlFor="provider-credential-mode">
                                    {t("app.admin.dashboard.model.hub.page.k1947a36f")}
                                </Label>
                                <input type="hidden" name="credentialMode" value={providerCredentialMode} />
                                <Select
                                    value={providerCredentialMode}
                                    onValueChange={(value: "apiKey" | "oauthFile") =>
                                        onDraftChange((draft) => ({ ...draft, providerCredentialMode: value }))
                                    }
                                >
                                    <SelectTrigger id="provider-credential-mode">
                                        <SelectValue>
                                            {resolveAdminLabel(t, "providerCredentialMode", providerCredentialMode)}
                                        </SelectValue>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {getAdminOptions("providerCredentialMode").map((option) => (
                                            <SelectItem key={option.value} value={option.value}>
                                                {t(option.labelKey)}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        ) : (
                            <input type="hidden" name="credentialMode" value="oauthFile" />
                        )}
                        {platformProviderSelected || providerCredentialMode === "oauthFile" ? (
                            <div className="space-y-2">
                                <Label htmlFor="provider-oauth-path">
                                    {t("app.admin.dashboard.model.hub.page.k686313b2")}
                                </Label>
                                <div className="flex items-center rounded-xl border border-input bg-background focus-within:ring-2 focus-within:ring-ring">
                                    <span className="shrink-0 border-r border-border/60 px-3 text-sm text-muted-foreground">
                                        oauth:
                                    </span>
                                    <Input
                                        id="provider-oauth-path"
                                        name="oauthPath"
                                        className="border-0 shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
                                        value={providerOauthPath}
                                        onChange={(event) =>
                                            onDraftChange((draft) => ({
                                                ...draft,
                                                providerOauthPath: event.target.value,
                                            }))
                                        }
                                        placeholder={activePlatformPreset.oauthPath}
                                    />
                                </div>
                                <p
                                    className={`text-xs ${(platformProviderSelected ? activePlatformPreset.supportState === "preset-only" : providerApiStandard === "gemini") ? "text-amber-600" : "text-muted-foreground"}`}
                                >
                                    {oauthHint}
                                </p>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <Label htmlFor="provider-api-key">
                                    {t("admin.enums.providerCredentialMode.apiKey")}
                                </Label>
                                <Input
                                    id="provider-api-key"
                                    name="apiKey"
                                    type="password"
                                    value={providerApiKey}
                                    onChange={(event) =>
                                        onDraftChange((draft) => ({ ...draft, providerApiKey: event.target.value }))
                                    }
                                    placeholder={providerType === "LOCAL" ? localBackendConfig.apiKey : ""}
                                />
                                {providerType === "LOCAL" ? (
                                    <p className="text-xs text-muted-foreground">{t(localBackendConfig.helpText)}</p>
                                ) : null}
                            </div>
                        )}
                    </fieldset>
                    <div className="admin-editor-footer">
                        {entitySaveError ? (
                            <p role="alert" className="text-sm text-destructive">
                                {entitySaveError}
                            </p>
                        ) : null}
                        <Button type="submit" disabled={entitySaving} className="w-full">
                            {t("app.admin.dashboard.model.hub.page.k93b84c67")}
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
}
