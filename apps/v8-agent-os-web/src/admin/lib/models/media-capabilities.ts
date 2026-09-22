export type MediaCapabilityOption = {
    id: string;
    labelKey: string;
    operationKind: string;
};

const MEDIA_CAPABILITY_OPTIONS: Record<string, readonly MediaCapabilityOption[]> = {
    IMAGE: [
        { id: "image.text_to_image", labelKey: "app.admin.dashboard.model.hub.capability.image.textToImage", operationKind: "image.generate" },
        { id: "image.image_to_image", labelKey: "app.admin.dashboard.model.hub.capability.image.imageToImage", operationKind: "image.edit" },
        { id: "image.edit", labelKey: "app.admin.dashboard.model.hub.capability.image.edit", operationKind: "image.edit" },
    ],
    VIDEO: [
        { id: "video.text_to_video", labelKey: "app.admin.dashboard.model.hub.capability.video.textToVideo", operationKind: "video.text_to_video" },
        { id: "video.image_to_video", labelKey: "app.admin.dashboard.model.hub.capability.video.imageToVideo", operationKind: "video.image_to_video" },
        { id: "video.first_last_frame", labelKey: "app.admin.dashboard.model.hub.capability.video.firstLastFrame", operationKind: "video.first_last_frame" },
        { id: "video.image_reference", labelKey: "app.admin.dashboard.model.hub.capability.video.imageReference", operationKind: "video.reference_to_video" },
        { id: "video.multimodal_reference", labelKey: "app.admin.dashboard.model.hub.capability.video.multimodalReference", operationKind: "video.reference_to_video" },
    ],
    AUDIO: [
        { id: "voice.tts", labelKey: "app.admin.dashboard.model.hub.capability.voice.tts", operationKind: "voice.tts" },
        { id: "voice.design", labelKey: "app.admin.dashboard.model.hub.capability.voice.design", operationKind: "voice.design" },
    ],
    VOICE: [
        { id: "voice.tts", labelKey: "app.admin.dashboard.model.hub.capability.voice.tts", operationKind: "voice.tts" },
        { id: "voice.design", labelKey: "app.admin.dashboard.model.hub.capability.voice.design", operationKind: "voice.design" },
    ],
    MUSIC: [
        { id: "music.generate", labelKey: "app.admin.dashboard.model.hub.capability.music.generate", operationKind: "music.generate" },
        { id: "music.cover", labelKey: "app.admin.dashboard.model.hub.capability.music.cover", operationKind: "music.cover" },
    ],
    MODEL3D: [
        { id: "model3d.text_to_3d", labelKey: "app.admin.dashboard.model.hub.capability.model3d.textTo3d", operationKind: "model3d.generate" },
        { id: "model3d.image_to_3d", labelKey: "app.admin.dashboard.model.hub.capability.model3d.imageTo3d", operationKind: "model3d.generate" },
    ],
    WORKFLOW: [
        { id: "video.action_transfer", labelKey: "app.admin.dashboard.model.hub.capability.workflow.actionTransfer", operationKind: "video.action_transfer" },
    ],
};

const PRIMARY_MODE_BY_OPERATION_KIND: Record<string, string> = {
    "image.generate": "image.text_to_image",
    "image.edit": "image.image_to_image",
    "video.text_to_video": "video.text_to_video",
    "video.image_to_video": "video.image_to_video",
    "video.first_last_frame": "video.first_last_frame",
    "video.reference_to_video": "video.image_reference",
    "voice.tts": "voice.tts",
    "voice.design": "voice.design",
    "music.generate": "music.generate",
    "music.cover": "music.cover",
    "model3d.generate": "model3d.text_to_3d",
    "video.action_transfer": "video.action_transfer",
};

function asStringList(value: unknown): string[] {
    if (!Array.isArray(value)) return [];
    return value
        .map((item) => String(item || "").trim())
        .filter(Boolean);
}

export function getMediaCapabilityOptions(modelType: string): readonly MediaCapabilityOption[] {
    return MEDIA_CAPABILITY_OPTIONS[String(modelType || "").trim().toUpperCase()] || [];
}

export function resolveMediaCapabilityModes(
    modelType: string,
    capabilityModes: unknown,
    operationKinds: unknown,
    operationCapabilityProfiles?: unknown,
): string[] {
    const options = getMediaCapabilityOptions(modelType);
    const allowed = new Set(options.map((option) => option.id));
    if (Array.isArray(capabilityModes)) {
        return Array.from(new Set(asStringList(capabilityModes).filter((mode) => allowed.has(mode))));
    }

    const normalizedOperationKinds = asStringList(operationKinds);
    const inferred = normalizedOperationKinds
        .map((operationKind) => PRIMARY_MODE_BY_OPERATION_KIND[operationKind])
        .filter((mode): mode is string => Boolean(mode && allowed.has(mode)));
    if (normalizedOperationKinds.includes("video.reference_to_video") && operationCapabilityProfiles && typeof operationCapabilityProfiles === "object") {
        const profiles = operationCapabilityProfiles as Record<string, unknown>;
        const referenceProfile = profiles["video.reference_to_video"];
        if (referenceProfile && typeof referenceProfile === "object") {
            const profile = referenceProfile as Record<string, unknown>;
            const inputModalities = new Set(asStringList(profile.inputModalities));
            const referenceInputs = profile.referenceInputs && typeof profile.referenceInputs === "object"
                ? profile.referenceInputs as Record<string, unknown>
                : {};
            if (("image" in referenceInputs || inputModalities.has("image")) && allowed.has("video.image_reference")) {
                inferred.push("video.image_reference");
            }
            if (("video" in referenceInputs || "audio" in referenceInputs || inputModalities.has("video") || inputModalities.has("audio"))
                && allowed.has("video.multimodal_reference")) {
                inferred.push("video.multimodal_reference");
            }
        }
    }
    if (inferred.length) return Array.from(new Set(inferred));
    return options[0] ? [options[0].id] : [];
}

export function deriveMediaOperationKinds(modelType: string, capabilityModes: unknown): string[] {
    const options = getMediaCapabilityOptions(modelType);
    const selected = new Set(asStringList(capabilityModes));
    return Array.from(new Set(
        options
            .filter((option) => selected.has(option.id))
            .map((option) => option.operationKind),
    ));
}
