"use client";
import { createModelDraft } from "@/lib/model-hub/editor-drafts";
import { readJsonErrorMessage } from "@/lib/model-hub/errors";
import { type FormEvent, useState, useMemo, useRef, useEffect } from "react";
import { type AIModel, type AIProvider } from "@/lib/model-hub/types";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { Label } from "@/components/ui/label";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { type ModelWireProtocol, MODEL_WIRE_PROTOCOLS } from "@/lib/model-hub/channels";
import { Input } from "@/components/ui/input";
import {
    resolveMediaCapabilityModes,
    getMediaCapabilityOptions,
    deriveMediaOperationKinds,
} from "@/lib/models/media-capabilities";
import { resolveAdminLabel } from "@/lib/admin-labels";
import { OutputTokenBudgetField } from "@/components/models/OutputTokenBudgetField";
import { RETRIEVAL_MODEL_TYPES, MEDIA_MODEL_TYPES } from "@/lib/model-hub/models";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

import type { ControlPlaneModel } from "@/components/models/control-plane-types";

type Props = {
    target: AIModel | null;
    providers: AIProvider[];
    controlMeta: ControlPlaneModel | null;
    onSaved: () => void | Promise<void>;
    onClose: () => void;
};

export function ModelEditorDialog({ target: editingModel, providers, controlMeta, onSaved, onClose }: Props) {
    const t = useT();
    const [draft, onDraftChange] = useState(() => createModelDraft(editingModel, controlMeta, providers));
    const entitySaveBusy = useRef(false);
    const [entitySaving, setEntitySaving] = useState(false);
    const [entitySaveError, setEntitySaveError] = useState("");
    const lifetime = useRef<AbortController | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        lifetime.current = controller;
        return () => controller.abort();
    }, []);

    const { toast } = useToast();
    const {
        modelType,
        mediaCapabilityModes,
        modelProviderId,
        modelChannelId,
        modelWireProtocol,
        comfyWorkflow,
        rerankApiFlavor,
    } = draft;
    const workflowImport = useRef<File | null>(null);
    useEffect(
        () => () => {
            workflowImport.current = null;
        },
        [editingModel?.providerId, editingModel?.id],
    );
    const selectedModelProvider = useMemo(
        () => providers.find((provider) => provider.id === modelProviderId) || null,
        [modelProviderId, providers],
    );
    const selectedModelChannel = useMemo(
        () =>
            selectedModelProvider?.channels?.find((channel) => channel.id === modelChannelId) ||
            selectedModelProvider?.channels?.find((channel) => channel.id === selectedModelProvider.defaultChannelId) ||
            selectedModelProvider?.channels?.[0] ||
            null,
        [modelChannelId, selectedModelProvider],
    );
    const handleSaveModel = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (entitySaveBusy.current) return;
        entitySaveBusy.current = true;
        const signal = lifetime.current?.signal;
        setEntitySaving(true);
        setEntitySaveError("");
        try {
            const formData = new FormData(event.currentTarget);
            const payload: Record<string, unknown> = {
                ...Object.fromEntries(formData.entries()),
                mediaLimits: editingModel?.mediaLimits || undefined,
                endpointBinding: editingModel?.endpointBinding || undefined,
                channelId: modelChannelId,
            };
            if (modelType === "WORKFLOW") {
                let prompt: Record<string, unknown>;
                try {
                    const parsed = JSON.parse(comfyWorkflow.promptJson || "null");
                    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("invalid");
                    prompt = parsed as Record<string, unknown>;
                } catch {
                    toast({
                        variant: "destructive",
                        title: t("app.admin.dashboard.model.hub.comfy.workflowInvalidTitle"),
                        description: t("app.admin.dashboard.model.hub.comfy.workflowInvalid"),
                    });
                    return;
                }
                const requiredBindings = [
                    comfyWorkflow.imageNodeId,
                    comfyWorkflow.imageInputName,
                    comfyWorkflow.videoNodeId,
                    comfyWorkflow.videoInputName,
                    comfyWorkflow.outputNodeId,
                    comfyWorkflow.outputField,
                ];
                if (requiredBindings.some((value) => !value.trim())) {
                    toast({
                        variant: "destructive",
                        title: t("app.admin.dashboard.model.hub.comfy.workflowInvalidTitle"),
                        description: t("app.admin.dashboard.model.hub.comfy.bindingsRequired"),
                    });
                    return;
                }
                payload.mediaLimits = {
                    ...(editingModel?.mediaLimits || {}),
                    comfyuiWorkflow: {
                        schema: "v8.comfyui.workflow.v1",
                        operationKind: "video.action_transfer",
                        prompt,
                        bindings: {
                            image: {
                                nodeId: comfyWorkflow.imageNodeId.trim(),
                                inputName: comfyWorkflow.imageInputName.trim(),
                            },
                            video: {
                                nodeId: comfyWorkflow.videoNodeId.trim(),
                                inputName: comfyWorkflow.videoInputName.trim(),
                            },
                        },
                        output: {
                            nodeId: comfyWorkflow.outputNodeId.trim(),
                            field: comfyWorkflow.outputField.trim(),
                            index: 0,
                        },
                    },
                };
            }
            if (["TEXT", "MULTIMODAL", "VISION", "CHAT"].includes(modelType)) {
                payload.wireProtocol = modelWireProtocol;
            }
            if (getMediaCapabilityOptions(modelType).length > 0) {
                payload.capabilityModes = mediaCapabilityModes;
            }
            for (const key of ["contextWindow", "maxTokens"]) {
                if (payload[key] === "") payload[key] = null;
            }
            const url = editingModel
                ? `/api/models/${encodeURIComponent(editingModel.id)}?providerId=${encodeURIComponent(editingModel.providerId)}`
                : "/api/models";
            const method = editingModel ? "PUT" : "POST";
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
            <DialogContent guardUnsaved className="admin-editor-modal">
                <DialogHeader>
                    <DialogTitle>
                        {editingModel
                            ? t("app.admin.dashboard.model.hub.page.k37053cf7")
                            : t("app.admin.dashboard.model.hub.page.k82b1063c")}
                    </DialogTitle>
                </DialogHeader>
                <form onSubmit={handleSaveModel} className="admin-editor-form">
                    <fieldset disabled={entitySaving} className="admin-editor-body space-y-4">
                        {providers.length === 0 ? (
                            <EmptyState
                                title={t("app.admin.dashboard.model.hub.page.k5ca95d1d")}
                                description={t("app.admin.dashboard.model.hub.page.k4119e026")}
                            />
                        ) : null}
                        <div className="space-y-2">
                            <Label htmlFor="model-provider">{t("app.admin.dashboard.model.hub.page.kc9371614")}</Label>
                            <input type="hidden" name="providerId" value={modelProviderId} />
                            <Select
                                value={modelProviderId}
                                onValueChange={(value) => {
                                    const provider = providers.find((item) => item.id === value);
                                    const channel =
                                        provider?.channels?.find((item) => item.id === provider.defaultChannelId) ||
                                        provider?.channels?.[0];
                                    onDraftChange((draft) => ({
                                        ...draft,
                                        modelProviderId: value,
                                        modelChannelId: channel?.id || "",
                                        modelWireProtocol: (channel?.defaultWireProtocol || "") as ModelWireProtocol,
                                    }));
                                }}
                            >
                                <SelectTrigger id="model-provider">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {providers.map((provider) => (
                                        <SelectItem key={provider.id} value={provider.id}>
                                            {provider.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-channel">
                                {t("app.admin.dashboard.model.hub.channel.modelChannel")}
                            </Label>
                            <select
                                id="model-channel"
                                name="channelId"
                                value={modelChannelId || selectedModelChannel?.id || ""}
                                onChange={(event) => {
                                    const channel = selectedModelProvider?.channels?.find(
                                        (item) => item.id === event.target.value,
                                    );
                                    onDraftChange((draft) => ({
                                        ...draft,
                                        modelChannelId: event.target.value,
                                        modelWireProtocol: (channel?.defaultWireProtocol || "") as ModelWireProtocol,
                                    }));
                                }}
                                disabled={!selectedModelProvider?.channels?.length}
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                            >
                                {(selectedModelProvider?.channels || []).map((channel) => (
                                    <option key={channel.id} value={channel.id}>
                                        {channel.label} · {channel.apiStandard} · {channel.baseUrl}
                                    </option>
                                ))}
                            </select>
                            <p className="text-xs leading-5 text-muted-foreground">
                                {t("app.admin.dashboard.model.hub.channel.modelChannelHelp")}
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-model-id">{t("app.admin.dashboard.model.hub.page.k8dbca6d6")}</Label>
                            <Input
                                id="model-model-id"
                                name="modelId"
                                defaultValue={editingModel?.modelId || ""}
                                required
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-type">{t("app.admin.dashboard.model.hub.page.k0bce4283")}</Label>
                            <input type="hidden" name="type" value={modelType} />
                            <Select
                                value={modelType}
                                onValueChange={(value) => {
                                    onDraftChange((draft) => ({
                                        ...draft,
                                        modelType: value,
                                        mediaCapabilityModes: resolveMediaCapabilityModes(value, undefined, []),
                                    }));
                                }}
                            >
                                <SelectTrigger id="model-type">
                                    <SelectValue>{resolveAdminLabel(t, "modelType", modelType)}</SelectValue>
                                </SelectTrigger>
                                <SelectContent>
                                    {[
                                        "TEXT",
                                        "MULTIMODAL",
                                        "IMAGE",
                                        "VIDEO",
                                        "AUDIO",
                                        "VOICE",
                                        "MUSIC",
                                        "WORKFLOW",
                                        "MODEL3D",
                                        "MEDIA",
                                        "EMBEDDING",
                                        "RERANK",
                                    ].map((value) => (
                                        <SelectItem key={value} value={value}>
                                            {resolveAdminLabel(t, "modelType", value)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        {modelType === "RERANK" ? (
                            <div className="space-y-2">
                                <Label htmlFor="model-rerank-flavor">
                                    {t("app.admin.dashboard.model.hub.page.k51b60583")}
                                </Label>
                                <input type="hidden" name="rerankApiFlavor" value={rerankApiFlavor} />
                                <Select
                                    value={rerankApiFlavor}
                                    onValueChange={(value) =>
                                        onDraftChange((draft) => ({ ...draft, rerankApiFlavor: value }))
                                    }
                                >
                                    <SelectTrigger id="model-rerank-flavor">
                                        <SelectValue>
                                            {resolveAdminLabel(t, "rerankApiFlavor", rerankApiFlavor)}
                                        </SelectValue>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="generic">
                                            {resolveAdminLabel(t, "rerankApiFlavor", "generic")}
                                        </SelectItem>
                                        <SelectItem value="vllm">
                                            {resolveAdminLabel(t, "localBackendPreset", "vllm")}
                                        </SelectItem>
                                        <SelectItem value="nexa">
                                            {resolveAdminLabel(t, "localBackendPreset", "nexa")}
                                        </SelectItem>
                                    </SelectContent>
                                </Select>
                                <p className="text-xs text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.page.kfaf657c9")}
                                </p>
                            </div>
                        ) : null}
                        {modelType === "TEXT" ||
                        modelType === "MULTIMODAL" ||
                        modelType === "VISION" ||
                        modelType === "CHAT" ? (
                            <div className="space-y-4">
                                <div className="grid gap-4 md:grid-cols-2">
                                    <div className="space-y-2">
                                        <Label htmlFor="model-context-window">
                                            {t("app.admin.dashboard.model.hub.page.k20e21cd2")}
                                        </Label>
                                        <Input
                                            id="model-context-window"
                                            name="contextWindow"
                                            type="number"
                                            min={1}
                                            step={1}
                                            defaultValue={editingModel?.contextWindow ?? ""}
                                            placeholder={t(
                                                "app.admin.dashboard.model.hub.page.contextWindowPlaceholder",
                                            )}
                                        />
                                    </div>
                                    <OutputTokenBudgetField
                                        key={editingModel?.id || "new"}
                                        id="model-max-tokens"
                                        model={editingModel}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="model-wire-protocol">
                                        {t("app.admin.dashboard.model.hub.page.wireProtocol")}
                                    </Label>
                                    <select
                                        id="model-wire-protocol"
                                        name="wireProtocol"
                                        value={modelWireProtocol}
                                        onChange={(event) =>
                                            onDraftChange((draft) => ({
                                                ...draft,
                                                modelWireProtocol: event.target.value as ModelWireProtocol,
                                            }))
                                        }
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    >
                                        <option value="">
                                            {t("app.admin.dashboard.model.hub.page.wireProtocolAuto")}
                                        </option>
                                        {MODEL_WIRE_PROTOCOLS.filter(
                                            (protocol) =>
                                                !selectedModelChannel?.wireProtocols?.length ||
                                                selectedModelChannel.wireProtocols.includes(protocol.id),
                                        ).map((protocol) => (
                                            <option key={protocol.id} value={protocol.id}>
                                                {t(protocol.labelKey)}
                                            </option>
                                        ))}
                                    </select>
                                    <p className="text-xs leading-5 text-muted-foreground">
                                        {t("app.admin.dashboard.model.hub.page.wireProtocolHelp")}
                                    </p>
                                </div>
                            </div>
                        ) : RETRIEVAL_MODEL_TYPES.has(modelType) ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="model-context-window">
                                        {t("app.admin.dashboard.model.hub.page.retrievalInputWindow")}
                                    </Label>
                                    <Input
                                        id="model-context-window"
                                        name="contextWindow"
                                        type="number"
                                        defaultValue={editingModel?.contextWindow ?? ""}
                                        placeholder={t(
                                            "app.admin.dashboard.model.hub.page.retrievalInputWindowPlaceholder",
                                        )}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="model-max-tokens">
                                        {t("app.admin.dashboard.model.hub.page.retrievalMaxTokens")}
                                    </Label>
                                    <Input
                                        id="model-max-tokens"
                                        name="maxTokens"
                                        type="number"
                                        defaultValue={editingModel?.maxTokens ?? ""}
                                        placeholder={t(
                                            "app.admin.dashboard.model.hub.page.retrievalMaxTokensPlaceholder",
                                        )}
                                    />
                                </div>
                                <p className="md:col-span-2 text-xs text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.page.retrievalInputWindowHelp")}
                                </p>
                            </div>
                        ) : MEDIA_MODEL_TYPES.has(modelType) ? (
                            <div className="space-y-4">
                                <AdminHoverInfo
                                    content={t("app.admin.dashboard.model.hub.catalog.mediaModelNotice")}
                                    panelClassName="text-xs leading-5"
                                >
                                    <Badge variant="secondary">
                                        {t("app.admin.dashboard.model.hub.catalog.mediaModelNoticeTitle")}
                                    </Badge>
                                </AdminHoverInfo>
                                {modelType !== "WORKFLOW" ? (
                                    <div className="grid gap-4 md:grid-cols-2">
                                        <div className="space-y-2">
                                            <Label htmlFor="model-endpoint-path">
                                                {t("app.admin.dashboard.model.hub.page.manualEndpointPath")}
                                            </Label>
                                            <Input
                                                id="model-endpoint-path"
                                                name="endpointPath"
                                                defaultValue={String(
                                                    editingModel?.endpointBinding?.endpointPath ||
                                                        editingModel?.mediaLimits?.endpointPath ||
                                                        "",
                                                )}
                                                placeholder="images/generations"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="model-provider-model-id">
                                                {t("app.admin.dashboard.model.hub.page.manualProviderModelId")}
                                            </Label>
                                            <Input
                                                id="model-provider-model-id"
                                                name="providerModelId"
                                                defaultValue={String(
                                                    editingModel?.endpointBinding?.providerModelId ||
                                                        editingModel?.mediaLimits?.providerModelId ||
                                                        "",
                                                )}
                                                placeholder="gpt-image-2"
                                            />
                                        </div>
                                    </div>
                                ) : null}
                                {modelType === "WORKFLOW" ? (
                                    <div className="space-y-2">
                                        <Label>{t("app.admin.dashboard.model.hub.page.mediaAdapter")}</Label>
                                        <input type="hidden" name="adapter" value="comfyui_workflow" />
                                        <div className="flex h-10 items-center rounded-md border border-input bg-muted/30 px-3 text-sm">
                                            ComfyUI Workflow
                                        </div>
                                    </div>
                                ) : (
                                    <div className="space-y-2">
                                        <Label htmlFor="model-media-adapter">
                                            {t("app.admin.dashboard.model.hub.page.mediaAdapter")}
                                        </Label>
                                        <select
                                            id="model-media-adapter"
                                            name="adapter"
                                            defaultValue={String(
                                                editingModel?.endpointBinding?.adapter ||
                                                    editingModel?.mediaLimits?.adapter ||
                                                    "",
                                            )}
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                        >
                                            <option value="">
                                                {t("app.admin.dashboard.model.hub.page.mediaAdapterUnbound")}
                                            </option>
                                            <option value="openai_images">OpenAI Images</option>
                                            <option value="agnes_images">Agnes Images</option>
                                            <option value="agnes_video">Agnes Video</option>
                                            <option value="volcengine_ark">Volcengine Ark</option>
                                            <option value="dashscope">Alibaba Cloud Model Studio</option>
                                            <option value="comfyui_workflow">ComfyUI Workflow</option>
                                            <option value="minimax_video">MiniMax Video</option>
                                            <option value="minimax_tts">MiniMax Speech</option>
                                            <option value="minimax_music">MiniMax Music</option>
                                            <option value="mureka_music">Mureka Music</option>
                                            <option value="v8_audio_tts">V8OS System Speech</option>
                                            <option value="tencent_hunyuan_3d">Tencent Hunyuan 3D</option>
                                            <option value="catalog_only">
                                                {t("app.admin.dashboard.model.hub.page.mediaAdapterCatalogOnly")}
                                            </option>
                                        </select>
                                        <p className="text-xs leading-5 text-muted-foreground">
                                            {t("app.admin.dashboard.model.hub.page.mediaAdapterHelp")}
                                        </p>
                                    </div>
                                )}
                                {getMediaCapabilityOptions(modelType).length > 0 ? (
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between gap-3">
                                            <Label>{t("app.admin.dashboard.model.hub.capability.title")}</Label>
                                            <span className="text-[11px] text-muted-foreground">
                                                {t("app.admin.dashboard.model.hub.capability.selectedCount", {
                                                    count: mediaCapabilityModes.length,
                                                })}
                                            </span>
                                        </div>
                                        <div className="flex flex-wrap gap-1.5 rounded-lg border border-border/70 bg-muted/20 p-2">
                                            {getMediaCapabilityOptions(modelType).map((option) => {
                                                const checked = mediaCapabilityModes.includes(option.id);
                                                const isLastSelected = checked && mediaCapabilityModes.length === 1;
                                                return (
                                                    <label
                                                        key={option.id}
                                                        className={`inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors ${checked ? "border-primary/40 bg-primary/10 text-foreground" : "border-border/70 bg-background text-muted-foreground hover:text-foreground"} ${isLastSelected ? "cursor-not-allowed opacity-70" : "cursor-pointer"}`}
                                                    >
                                                        <Checkbox
                                                            checked={checked}
                                                            disabled={isLastSelected}
                                                            onCheckedChange={(nextChecked) => {
                                                                onDraftChange((draft) => ({
                                                                    ...draft,
                                                                    mediaCapabilityModes:
                                                                        nextChecked === true
                                                                            ? Array.from(
                                                                                  new Set([
                                                                                      ...draft.mediaCapabilityModes,
                                                                                      option.id,
                                                                                  ]),
                                                                              )
                                                                            : draft.mediaCapabilityModes.filter(
                                                                                  (item) => item !== option.id,
                                                                              ),
                                                                }));
                                                            }}
                                                            className="h-3.5 w-3.5 rounded-[3px]"
                                                        />
                                                        <span>{t(option.labelKey)}</span>
                                                    </label>
                                                );
                                            })}
                                        </div>
                                        <input
                                            type="hidden"
                                            name="operationKind"
                                            value={deriveMediaOperationKinds(modelType, mediaCapabilityModes)[0] || ""}
                                        />
                                        <p className="text-xs leading-5 text-muted-foreground">
                                            {t("app.admin.dashboard.model.hub.capability.help")}
                                        </p>
                                    </div>
                                ) : null}
                                {modelType === "WORKFLOW" ? (
                                    <div className="space-y-4 rounded-lg border border-border/70 p-3">
                                        <div className="space-y-2">
                                            <Label htmlFor="comfy-workflow-file">
                                                {t("app.admin.dashboard.model.hub.comfy.apiWorkflow")}
                                            </Label>
                                            <Input
                                                id="comfy-workflow-file"
                                                type="file"
                                                accept="application/json,.json"
                                                onChange={async (event) => {
                                                    const file = event.target.files?.[0];
                                                    workflowImport.current = file || null;
                                                    if (!file) return;
                                                    try {
                                                        const text = await file.text();
                                                        if (workflowImport.current !== file) return;
                                                        const parsed = JSON.parse(text) as Record<string, unknown>;
                                                        const prompt =
                                                            parsed.prompt && typeof parsed.prompt === "object"
                                                                ? parsed.prompt
                                                                : parsed;
                                                        if (
                                                            !prompt ||
                                                            Array.isArray(prompt) ||
                                                            typeof prompt !== "object"
                                                        )
                                                            throw new Error("invalid");
                                                        onDraftChange((draft) =>
                                                            draft.comfyWorkflow.promptJson !== comfyWorkflow.promptJson
                                                                ? draft
                                                                : {
                                                                      ...draft,
                                                                      comfyWorkflow: {
                                                                          ...draft.comfyWorkflow,
                                                                          promptJson: JSON.stringify(prompt, null, 2),
                                                                      },
                                                                  },
                                                        );
                                                    } catch {
                                                        if (workflowImport.current !== file) return;
                                                        toast({
                                                            variant: "destructive",
                                                            title: t(
                                                                "app.admin.dashboard.model.hub.comfy.workflowInvalidTitle",
                                                            ),
                                                            description: t(
                                                                "app.admin.dashboard.model.hub.comfy.workflowInvalid",
                                                            ),
                                                        });
                                                    }
                                                }}
                                            />
                                            <Textarea
                                                value={comfyWorkflow.promptJson}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            promptJson: event.target.value,
                                                        },
                                                    }))
                                                }
                                                className="max-h-48 min-h-24 font-mono text-xs"
                                                aria-label={t("app.admin.dashboard.model.hub.comfy.apiWorkflowJson")}
                                            />
                                        </div>
                                        <div className="grid gap-3 md:grid-cols-2">
                                            <Input
                                                value={comfyWorkflow.imageNodeId}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            imageNodeId: event.target.value,
                                                        },
                                                    }))
                                                }
                                                placeholder={t("app.admin.dashboard.model.hub.comfy.imageNodeId")}
                                            />
                                            <Input
                                                value={comfyWorkflow.imageInputName}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            imageInputName: event.target.value,
                                                        },
                                                    }))
                                                }
                                                placeholder={t("app.admin.dashboard.model.hub.comfy.imageInputName")}
                                            />
                                            <Input
                                                value={comfyWorkflow.videoNodeId}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            videoNodeId: event.target.value,
                                                        },
                                                    }))
                                                }
                                                placeholder={t("app.admin.dashboard.model.hub.comfy.videoNodeId")}
                                            />
                                            <Input
                                                value={comfyWorkflow.videoInputName}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            videoInputName: event.target.value,
                                                        },
                                                    }))
                                                }
                                                placeholder={t("app.admin.dashboard.model.hub.comfy.videoInputName")}
                                            />
                                            <Input
                                                value={comfyWorkflow.outputNodeId}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            outputNodeId: event.target.value,
                                                        },
                                                    }))
                                                }
                                                placeholder={t("app.admin.dashboard.model.hub.comfy.outputNodeId")}
                                            />
                                            <Input
                                                value={comfyWorkflow.outputField}
                                                onChange={(event) =>
                                                    onDraftChange((draft) => ({
                                                        ...draft,
                                                        comfyWorkflow: {
                                                            ...draft.comfyWorkflow,
                                                            outputField: event.target.value,
                                                        },
                                                    }))
                                                }
                                                placeholder={t("app.admin.dashboard.model.hub.comfy.outputField")}
                                            />
                                        </div>
                                    </div>
                                ) : null}
                                <p className="text-xs leading-5 text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.page.manualBindingHelp")}
                                </p>
                            </div>
                        ) : null}
                    </fieldset>
                    <div className="admin-editor-footer">
                        {entitySaveError ? (
                            <p role="alert" className="text-sm text-destructive">
                                {entitySaveError}
                            </p>
                        ) : null}
                        <Button type="submit" disabled={entitySaving} className="w-full">
                            {t("app.admin.dashboard.model.hub.page.kb7dfaded")}
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
}
