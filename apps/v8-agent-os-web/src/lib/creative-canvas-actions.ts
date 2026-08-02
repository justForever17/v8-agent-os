export type CreativeCanvasMediaType =
    | "image"
    | "video"
    | "audio"
    | "model_3d"
    | "psd"
    | "motion"
    | "document"
    | "text"
    | "mask"
    | "metadata"
    | "unknown";

export type CreativeCanvasActionTarget = "canvas" | "node" | "selection" | "edge";
export type CreativeCanvasActionScope = "local" | "manual" | "agent_only" | "governance";
export type CreativeCanvasExecutionClass =
    | "local_read"
    | "local_mutation"
    | "chat_task"
    | "agent_projection"
    | "governance_projection";
export type CreativeCanvasParameterEditor = "frame_pick" | "time_range" | "psd_composition" | "psd_layers";
export type CreativeCanvasOutputKind =
    | "none"
    | "canvas_state"
    | "source_derivative"
    | "artifact"
    | "artifacts"
    | "evidence"
    | "runtime_status";

export type CreativeCanvasSelectionItem = {
    id: string;
    mediaType: CreativeCanvasMediaType;
    /** Stable visual/data-flow order. Ordered actions reject duplicate or non-finite values. */
    order: number;
};

export type CreativeCanvasMediaCountConstraint = {
    mediaType: CreativeCanvasMediaType;
    min: number;
    max?: number;
};

export type CreativeCanvasSelectionConstraint = {
    targets: readonly CreativeCanvasActionTarget[];
    minItems: number;
    maxItems?: number;
    allowedMediaTypes?: readonly CreativeCanvasMediaType[];
    mediaCounts?: readonly CreativeCanvasMediaCountConstraint[];
    sameMediaType?: boolean;
    ordered?: boolean;
};

export type CreativeCanvasActionOutput = {
    kind: CreativeCanvasOutputKind;
    /** Stable logical output slot used to reserve and reconcile canvas cards. */
    slot: string;
    mediaTypes: readonly CreativeCanvasMediaType[];
};

export type CreativeCanvasActionBinding =
    | {
        kind: "canvas_message";
        action: string;
    }
    | {
        kind: "creative_media";
        capability: string;
    }
    | {
        kind: "mediakit";
        pluginId: "volcengine-mediakit";
        componentId: "mediakit-cli";
        domain: "editing" | "image" | "audio" | "video";
        action: string;
    }
    | {
        kind: "runtime_event";
        action: string;
    };

export type CreativeCanvasAction = {
    actionId: string;
    labelKey: string;
    descriptionKey: string;
    scope: CreativeCanvasActionScope;
    selection: CreativeCanvasSelectionConstraint;
    requiresPrompt: boolean;
    requiresMask: boolean;
    parameterEditor?: CreativeCanvasParameterEditor;
    output: CreativeCanvasActionOutput;
    executionClass: CreativeCanvasExecutionClass;
    networkRequired: boolean;
    mayIncurCost: boolean;
    requiresGrant: boolean;
    availableWhileRunning: boolean;
    binding?: CreativeCanvasActionBinding;
};

export type CreativeCanvasActionContext = {
    target: CreativeCanvasActionTarget;
    selection: readonly CreativeCanvasSelectionItem[];
    sessionRunning: boolean;
    pluginAvailable: boolean;
    pluginGranted: boolean;
    /** A direct user action may request a one-run minimal grant during normal chat submission. */
    allowPluginGrantRequest?: boolean;
    /** Optional capability snapshot. Omit it when only plugin-level status is known. */
    availablePluginActionIds?: readonly string[] | ReadonlySet<string>;
    /** Agent/governance actions are projections, not context-menu entries. */
    includeNonInteractive?: boolean;
};

const ALL_MEDIA_TYPES: readonly CreativeCanvasMediaType[] = [
    "image",
    "video",
    "audio",
    "model_3d",
    "psd",
    "motion",
    "document",
    "text",
    "mask",
    "metadata",
    "unknown",
];

const VISUAL_REFERENCE_TYPES: readonly CreativeCanvasMediaType[] = ["image", "video"];
const DOWNLOADABLE_MEDIA_TYPES: readonly CreativeCanvasMediaType[] = ALL_MEDIA_TYPES;

function keys(actionId: string) {
    const prefix = `web.creativeCanvas.actions.${actionId}`;
    return {
        labelKey: `${prefix}.label`,
        descriptionKey: `${prefix}.description`,
    };
}

function defineAction(
    action: Omit<CreativeCanvasAction, "labelKey" | "descriptionKey">,
): CreativeCanvasAction {
    return { ...action, ...keys(action.actionId) };
}

function output(
    kind: CreativeCanvasOutputKind,
    slot: string,
    mediaTypes: readonly CreativeCanvasMediaType[] = [],
): CreativeCanvasActionOutput {
    return { kind, slot, mediaTypes };
}

function selection(
    targets: readonly CreativeCanvasActionTarget[],
    minItems: number,
    maxItems: number | undefined,
    allowedMediaTypes?: readonly CreativeCanvasMediaType[],
    options: Pick<CreativeCanvasSelectionConstraint, "mediaCounts" | "sameMediaType" | "ordered"> = {},
): CreativeCanvasSelectionConstraint {
    return {
        targets,
        minItems,
        ...(maxItems === undefined ? {} : { maxItems }),
        ...(allowedMediaTypes ? { allowedMediaTypes } : {}),
        ...options,
    };
}

const CANVAS_ONLY = selection(["canvas"], 0, 0);
const EDGE_ONLY = selection(["edge"], 0, 0);
const ONE_ANY = selection(["node", "selection"], 1, 1, ALL_MEDIA_TYPES);
const ONE_IMAGE = selection(["node", "selection"], 1, 1, ["image"]);
const ONE_VIDEO = selection(["node", "selection"], 1, 1, ["video"]);
const ONE_AUDIO = selection(["node", "selection"], 1, 1, ["audio"]);
const ONE_PSD = selection(["node", "selection"], 1, 1, ["psd"]);
const ONE_MODEL_3D = selection(["node", "selection"], 1, 1, ["model_3d"]);
const ONE_AUDIO_OR_VIDEO = selection(["node", "selection"], 1, 1, ["audio", "video"]);
const MOTION_AND_MODEL_3D = selection(["selection"], 2, 2, ["motion", "model_3d"], {
    mediaCounts: [{ mediaType: "motion", min: 1, max: 1 }, { mediaType: "model_3d", min: 1, max: 1 }],
    ordered: true,
});
const IMAGE_AND_VIDEO = selection(["selection"], 2, 2, ["image", "video"], {
    mediaCounts: [{ mediaType: "image", min: 1, max: 1 }, { mediaType: "video", min: 1, max: 1 }],
    ordered: true,
});
const ANY_SELECTION = selection(["node", "selection"], 1, undefined, ALL_MEDIA_TYPES, { ordered: true });
const ANY_CONTEXT = selection(["canvas", "node", "selection", "edge"], 0, undefined, ALL_MEDIA_TYPES);

const LOCAL_DEFAULTS = {
    scope: "local" as const,
    requiresPrompt: false,
    requiresMask: false,
    networkRequired: false,
    mayIncurCost: false,
    requiresGrant: false,
    availableWhileRunning: false,
};

export const LOCAL_CREATIVE_CANVAS_ACTIONS: readonly CreativeCanvasAction[] = [
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.view",
        selection: ONE_ANY,
        output: output("none", "viewer"),
        executionClass: "local_read",
        availableWhileRunning: true,
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.download",
        selection: selection(["node", "selection"], 1, undefined, DOWNLOADABLE_MEDIA_TYPES, { ordered: true }),
        output: output("none", "download"),
        executionClass: "local_read",
        availableWhileRunning: true,
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.open_in_file_manager",
        selection: ONE_ANY,
        output: output("none", "file_manager"),
        executionClass: "local_read",
        availableWhileRunning: true,
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.upload_sources",
        selection: CANVAS_ONLY,
        output: output("canvas_state", "sources", ALL_MEDIA_TYPES),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.open_artifact_tray",
        selection: CANVAS_ONLY,
        output: output("none", "artifact_tray"),
        executionClass: "local_read",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.pull_artifact_to_canvas",
        selection: CANVAS_ONLY,
        output: output("canvas_state", "artifact_node", ALL_MEDIA_TYPES),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.fit_view",
        selection: CANVAS_ONLY,
        output: output("canvas_state", "viewport"),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.set_reference",
        selection: ANY_SELECTION,
        output: output("canvas_state", "reference_set"),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.start_connection",
        selection: ONE_ANY,
        output: output("canvas_state", "connection_draft"),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.connect_selection",
        selection: selection(["selection"], 2, undefined, ALL_MEDIA_TYPES, { ordered: true }),
        output: output("canvas_state", "connections"),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.create_mask",
        selection: ONE_IMAGE,
        output: output("source_derivative", "mask", ["mask"]),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.edit_mask",
        selection: selection(["node", "selection"], 1, 1, ["image", "mask"]),
        output: output("source_derivative", "mask", ["mask"]),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.delete_selection",
        selection: ANY_SELECTION,
        output: output("canvas_state", "selection"),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.delete_connection",
        selection: EDGE_ONLY,
        output: output("canvas_state", "connection"),
        executionClass: "local_mutation",
    }),
    defineAction({
        ...LOCAL_DEFAULTS,
        actionId: "local.clear_canvas",
        selection: CANVAS_ONLY,
        output: output("canvas_state", "canvas"),
        executionClass: "local_mutation",
    }),
];

const MANUAL_MESSAGE_DEFAULTS = {
    scope: "manual" as const,
    requiresMask: false,
    executionClass: "chat_task" as const,
    networkRequired: false,
    mayIncurCost: false,
    requiresGrant: false,
    availableWhileRunning: false,
};

export const CANVAS_MESSAGE_ACTIONS: readonly CreativeCanvasAction[] = [
    defineAction({
        ...MANUAL_MESSAGE_DEFAULTS,
        actionId: "message.submit_selection",
        selection: ANY_SELECTION,
        requiresPrompt: true,
        output: output("runtime_status", "chat_run"),
        binding: { kind: "canvas_message", action: "submit_selection" },
    }),
    defineAction({
        ...MANUAL_MESSAGE_DEFAULTS,
        actionId: "message.comment_connection",
        selection: EDGE_ONLY,
        requiresPrompt: true,
        output: output("runtime_status", "chat_run"),
        binding: { kind: "canvas_message", action: "comment_connection" },
    }),
];

function creativeMediaAction(input: {
    actionId: string;
    capability: string;
    selection: CreativeCanvasSelectionConstraint;
    requiresPrompt: boolean;
    requiresMask?: boolean;
    parameterEditor?: CreativeCanvasParameterEditor;
    networkRequired?: boolean;
    mayIncurCost?: boolean;
    output: CreativeCanvasActionOutput;
}): CreativeCanvasAction {
    return defineAction({
        actionId: input.actionId,
        scope: "manual",
        selection: input.selection,
        requiresPrompt: input.requiresPrompt,
        requiresMask: input.requiresMask ?? false,
        parameterEditor: input.parameterEditor,
        output: input.output,
        executionClass: "chat_task",
        networkRequired: input.networkRequired ?? true,
        mayIncurCost: input.mayIncurCost ?? true,
        requiresGrant: false,
        availableWhileRunning: false,
        binding: { kind: "creative_media", capability: input.capability },
    });
}

const OPTIONAL_REFERENCES = selection(
    ["canvas", "node", "selection"],
    0,
    8,
    ["image", "video", "audio", "model_3d", "psd", "document", "text"],
    { ordered: true },
);

export const CREATIVE_MEDIA_NATIVE_ACTIONS: readonly CreativeCanvasAction[] = [
    creativeMediaAction({
        actionId: "creative_media.generate_image",
        capability: "image.generate",
        selection: OPTIONAL_REFERENCES,
        requiresPrompt: true,
        output: output("artifact", "image", ["image"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.edit_image",
        capability: "image.edit",
        selection: ONE_IMAGE,
        requiresPrompt: true,
        output: output("artifact", "image_derivative", ["image"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.edit_image_region",
        capability: "image.edit",
        selection: selection(["selection"], 2, 2, ["image", "mask"], {
            mediaCounts: [
                { mediaType: "image", min: 1, max: 1 },
                { mediaType: "mask", min: 1, max: 1 },
            ],
        }),
        requiresPrompt: true,
        requiresMask: true,
        output: output("artifact", "image_derivative", ["image"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.generate_video",
        capability: "video.text_to_video",
        selection: selection(["canvas"], 0, 0),
        requiresPrompt: true,
        output: output("artifact", "video", ["video"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.animate_image",
        capability: "video.image_to_video",
        selection: ONE_IMAGE,
        requiresPrompt: true,
        output: output("artifact", "video", ["video"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.generate_video_from_keyframes",
        capability: "video.keyframe_to_video",
        selection: selection(["selection"], 2, 2, ["image"], { sameMediaType: true, ordered: true }),
        requiresPrompt: true,
        output: output("artifact", "video", ["video"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.generate_video_from_references",
        capability: "video.reference_to_video",
        selection: selection(["selection"], 1, 8, VISUAL_REFERENCE_TYPES, { ordered: true }),
        requiresPrompt: true,
        output: output("artifact", "video", ["video"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.generate_voice",
        capability: "voice.generate",
        selection: selection(["canvas", "node", "selection"], 0, 4, ["text", "document"], { ordered: true }),
        requiresPrompt: true,
        output: output("artifact", "voice", ["audio"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.generate_music",
        capability: "music.generate",
        selection: OPTIONAL_REFERENCES,
        requiresPrompt: true,
        output: output("artifact", "music", ["audio"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.generate_model_3d",
        capability: "model3d.generate",
        selection: selection(["canvas", "node", "selection"], 0, 4, ["image", "model_3d", "text"], { ordered: true }),
        requiresPrompt: true,
        output: output("artifact", "model_3d", ["model_3d"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.compose_psd",
        capability: "image.compose_psd",
        selection: selection(["node", "selection"], 1, 60, ["image", "psd"], { ordered: true }),
        requiresPrompt: false,
        parameterEditor: "psd_composition",
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "psd_document", ["psd"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.edit_psd_layers",
        capability: "image.edit_psd_layers",
        selection: ONE_PSD,
        requiresPrompt: false,
        parameterEditor: "psd_layers",
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "psd_document", ["psd"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.extract_video_frame_exact",
        capability: "video.extract_frame_exact",
        selection: ONE_VIDEO,
        requiresPrompt: false,
        parameterEditor: "frame_pick",
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "image_derivative", ["image"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.trim_video_exact",
        capability: "video.trim_exact",
        selection: ONE_VIDEO,
        requiresPrompt: false,
        parameterEditor: "time_range",
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "video_derivative", ["video"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.trim_audio_exact",
        capability: "audio.trim_exact",
        selection: ONE_AUDIO,
        requiresPrompt: false,
        parameterEditor: "time_range",
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "audio_derivative", ["audio"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.extract_holistic_motion",
        capability: "video.extract_holistic_motion",
        selection: ONE_VIDEO,
        requiresPrompt: false,
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "motion_clip", ["motion"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.transfer_action_to_character",
        capability: "video.action_transfer",
        selection: IMAGE_AND_VIDEO,
        requiresPrompt: false,
        networkRequired: true,
        mayIncurCost: true,
        output: output("artifact", "action_transfer_video", ["video"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.inspect_rigged_model",
        capability: "model3d.inspect_rigged",
        selection: ONE_MODEL_3D,
        requiresPrompt: false,
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "rig_profile", ["document"]),
    }),
    creativeMediaAction({
        actionId: "creative_media.retarget_motion_godot",
        capability: "model3d.retarget_motion_godot",
        selection: MOTION_AND_MODEL_3D,
        requiresPrompt: false,
        networkRequired: false,
        mayIncurCost: false,
        output: output("artifact", "animated_model", ["model_3d"]),
    }),
];

function mediaKitAction(input: {
    domain: "editing" | "image" | "audio" | "video";
    action: string;
    selection: CreativeCanvasSelectionConstraint;
    output: CreativeCanvasActionOutput;
    requiresPrompt?: boolean;
    parameterEditor?: CreativeCanvasParameterEditor;
    networkRequired?: boolean;
    mayIncurCost?: boolean;
}): CreativeCanvasAction {
    return defineAction({
        actionId: `mediakit.${input.domain}.${input.action}`,
        scope: "manual",
        selection: input.selection,
        requiresPrompt: input.requiresPrompt ?? false,
        requiresMask: false,
        parameterEditor: input.parameterEditor,
        output: input.output,
        executionClass: "chat_task",
        networkRequired: input.networkRequired ?? false,
        mayIncurCost: input.mayIncurCost ?? false,
        requiresGrant: true,
        availableWhileRunning: false,
        binding: {
            kind: "mediakit",
            pluginId: "volcengine-mediakit",
            componentId: "mediakit-cli",
            domain: input.domain,
            action: input.action,
        },
    });
}

const TWO_OR_MORE_AUDIO = selection(["selection"], 2, undefined, ["audio"], { sameMediaType: true, ordered: true });
const TWO_OR_MORE_VIDEO = selection(["selection"], 2, undefined, ["video"], { sameMediaType: true, ordered: true });
const ONE_OR_MORE_IMAGE = selection(["node", "selection"], 1, undefined, ["image"], { sameMediaType: true, ordered: true });
const ONE_OR_MORE_VIDEO = selection(["node", "selection"], 1, 100, ["video"], { sameMediaType: true, ordered: true });
const VIDEO_AND_IMAGE = selection(["selection"], 2, 2, ["video", "image"], {
    mediaCounts: [
        { mediaType: "video", min: 1, max: 1 },
        { mediaType: "image", min: 1, max: 1 },
    ],
});
const VIDEO_AND_AUDIO = selection(["selection"], 2, 2, ["video", "audio"], {
    mediaCounts: [
        { mediaType: "video", min: 1, max: 1 },
        { mediaType: "audio", min: 1, max: 1 },
    ],
});

export const MEDIAKIT_CREATIVE_CANVAS_ACTIONS: readonly CreativeCanvasAction[] = [
    mediaKitAction({ domain: "editing", action: "add-image-to-video", selection: VIDEO_AND_IMAGE, output: output("artifact", "video_derivative", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "add-subtitle-to-video", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), requiresPrompt: true }),
    mediaKitAction({ domain: "editing", action: "adjust-audio-speed", selection: ONE_AUDIO, output: output("artifact", "audio_derivative", ["audio"]) }),
    mediaKitAction({ domain: "editing", action: "adjust-video-speed", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "adjust-video-volume", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "apply-video-filter", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "concat-audio", selection: TWO_OR_MORE_AUDIO, output: output("artifact", "audio", ["audio"]) }),
    mediaKitAction({ domain: "editing", action: "concat-video", selection: TWO_OR_MORE_VIDEO, output: output("artifact", "video", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "extract-audio", selection: ONE_VIDEO, output: output("artifact", "audio", ["audio"]) }),
    mediaKitAction({ domain: "editing", action: "fade-audio", selection: ONE_AUDIO, output: output("artifact", "audio_derivative", ["audio"]) }),
    mediaKitAction({ domain: "editing", action: "fade-video-audio", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "flip-video", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "image-to-video", selection: ONE_OR_MORE_IMAGE, output: output("artifact", "video", ["video"]) }),
    mediaKitAction({ domain: "editing", action: "mix-audio", selection: TWO_OR_MORE_AUDIO, output: output("artifact", "audio", ["audio"]) }),
    mediaKitAction({ domain: "editing", action: "mux-audio-video", selection: VIDEO_AND_AUDIO, output: output("artifact", "video", ["video"]) }),
    mediaKitAction({ domain: "image", action: "image-ocr", selection: ONE_IMAGE, output: output("evidence", "recognized_text", ["text", "metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "image", action: "erase-image", selection: ONE_IMAGE, output: output("artifact", "image_derivative", ["image"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "image", action: "remove-image-background", selection: ONE_IMAGE, output: output("artifact", "image_derivative", ["image"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "image", action: "enhance-image", selection: ONE_IMAGE, output: output("artifact", "image_derivative", ["image"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "image", action: "evaluate-image-quality", selection: ONE_IMAGE, output: output("evidence", "quality_report", ["metadata"]), networkRequired: true, mayIncurCost: true }),

    mediaKitAction({ domain: "audio", action: "separate-voice", selection: ONE_AUDIO_OR_VIDEO, output: output("artifacts", "separated_audio", ["audio"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "audio", action: "probe-audio-metadata", selection: ONE_AUDIO, output: output("evidence", "media_metadata", ["metadata"]) }),

    mediaKitAction({ domain: "video", action: "analyze-video-highlights", selection: ONE_OR_MORE_VIDEO, output: output("evidence", "highlight_analysis", ["metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "analyze-video-storyline", selection: selection(["node", "selection"], 1, 30, ["video"], { sameMediaType: true, ordered: true }), output: output("evidence", "storyline", ["metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "asr-subtitles", selection: ONE_AUDIO_OR_VIDEO, output: output("evidence", "subtitles", ["text", "metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "enhance-video", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "enhance-video-generative", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "erase-video-subtitle", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "erase-video-subtitle-pro", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "generate-highlights-microdrama", selection: ONE_OR_MORE_VIDEO, output: output("artifacts", "highlight_package", ["video", "image", "metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "generate-highlights-minigame", selection: ONE_VIDEO, output: output("artifacts", "highlight_package", ["video", "metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "matte-greenscreen-video", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "matte-portrait-video", selection: ONE_VIDEO, output: output("artifact", "video_derivative", ["video"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "probe-video-metadata", selection: ONE_VIDEO, output: output("evidence", "media_metadata", ["metadata"]) }),
    mediaKitAction({ domain: "video", action: "segment-scenes", selection: ONE_VIDEO, output: output("artifacts", "scene_segments", ["video", "metadata"]), networkRequired: true, mayIncurCost: true }),
    mediaKitAction({ domain: "video", action: "video-ocr", selection: ONE_VIDEO, output: output("evidence", "recognized_text", ["text", "metadata"]), networkRequired: true, mayIncurCost: true }),
];

function projectionAction(input: {
    actionId: string;
    scope: "agent_only" | "governance";
    output?: CreativeCanvasActionOutput;
    networkRequired?: boolean;
    mayIncurCost?: boolean;
}): CreativeCanvasAction {
    return defineAction({
        actionId: input.actionId,
        scope: input.scope,
        selection: ANY_CONTEXT,
        requiresPrompt: false,
        requiresMask: false,
        output: input.output ?? output("runtime_status", "runtime_status"),
        executionClass: input.scope === "governance" ? "governance_projection" : "agent_projection",
        networkRequired: input.networkRequired ?? false,
        mayIncurCost: input.mayIncurCost ?? false,
        requiresGrant: false,
        availableWhileRunning: false,
        binding: { kind: "runtime_event", action: input.actionId },
    });
}

export const AGENT_CREATIVE_CANVAS_ACTIONS: readonly CreativeCanvasAction[] = [
    projectionAction({ actionId: "agent.discover_capabilities", scope: "agent_only" }),
    projectionAction({ actionId: "agent.compile_recipe", scope: "agent_only", output: output("evidence", "recipe", ["document", "metadata"]) }),
    projectionAction({ actionId: "agent.compile_work_order", scope: "agent_only", output: output("evidence", "work_order", ["document", "metadata"]) }),
    projectionAction({ actionId: "agent.register_assets", scope: "agent_only" }),
    projectionAction({ actionId: "agent.reserve_output_slots", scope: "agent_only", output: output("canvas_state", "output_slots") }),
    projectionAction({ actionId: "agent.create_job", scope: "agent_only", networkRequired: true, mayIncurCost: true }),
    projectionAction({ actionId: "agent.observe_job", scope: "agent_only", networkRequired: true }),
    projectionAction({ actionId: "agent.collect_artifacts", scope: "agent_only", output: output("artifacts", "job_artifacts", ALL_MEDIA_TYPES), networkRequired: true }),
    projectionAction({ actionId: "agent.retry_job", scope: "agent_only", networkRequired: true, mayIncurCost: true }),
    projectionAction({ actionId: "agent.create_edit_plan", scope: "agent_only", output: output("evidence", "edit_plan", ["document", "metadata"]) }),
    projectionAction({ actionId: "agent.render_edit", scope: "agent_only", output: output("artifacts", "render_outputs", ALL_MEDIA_TYPES), networkRequired: true, mayIncurCost: true }),
    projectionAction({ actionId: "agent.update_output_slot", scope: "agent_only", output: output("canvas_state", "output_slot") }),
];

export const GOVERNANCE_CREATIVE_CANVAS_ACTIONS: readonly CreativeCanvasAction[] = [
    projectionAction({ actionId: "governance.provider_lock", scope: "governance" }),
    projectionAction({ actionId: "governance.asset_ledger", scope: "governance" }),
    projectionAction({ actionId: "governance.sample_approval", scope: "governance" }),
    projectionAction({ actionId: "governance.quality_check", scope: "governance", output: output("evidence", "quality_report", ["metadata"]) }),
    projectionAction({ actionId: "governance.cost_ledger", scope: "governance", output: output("evidence", "cost_ledger", ["metadata"]) }),
    projectionAction({ actionId: "governance.safety_check", scope: "governance", output: output("evidence", "safety_report", ["metadata"]) }),
    projectionAction({ actionId: "governance.artifact_proof", scope: "governance", output: output("evidence", "artifact_proof", ["metadata"]) }),
    projectionAction({ actionId: "governance.lineage_check", scope: "governance", output: output("evidence", "lineage", ["metadata"]) }),
];

export const CREATIVE_CANVAS_ACTIONS: readonly CreativeCanvasAction[] = [
    ...LOCAL_CREATIVE_CANVAS_ACTIONS,
    ...CANVAS_MESSAGE_ACTIONS,
    ...CREATIVE_MEDIA_NATIVE_ACTIONS,
    ...MEDIAKIT_CREATIVE_CANVAS_ACTIONS,
    ...AGENT_CREATIVE_CANVAS_ACTIONS,
    ...GOVERNANCE_CREATIVE_CANVAS_ACTIONS,
];

function selectionMatches(
    constraint: CreativeCanvasSelectionConstraint,
    context: CreativeCanvasActionContext,
): boolean {
    if (!constraint.targets.includes(context.target)) return false;

    const selected = context.selection;
    if (selected.length < constraint.minItems) return false;
    if (constraint.maxItems !== undefined && selected.length > constraint.maxItems) return false;

    if (constraint.allowedMediaTypes) {
        const allowed = new Set(constraint.allowedMediaTypes);
        if (selected.some((item) => !allowed.has(item.mediaType))) return false;
    }

    if (constraint.sameMediaType && new Set(selected.map((item) => item.mediaType)).size > 1) return false;

    if (constraint.ordered) {
        const order = selected.map((item) => item.order);
        if (order.some((value) => !Number.isFinite(value)) || new Set(order).size !== order.length) return false;
    }

    if (constraint.mediaCounts) {
        for (const requirement of constraint.mediaCounts) {
            const count = selected.filter((item) => item.mediaType === requirement.mediaType).length;
            if (count < requirement.min) return false;
            if (requirement.max !== undefined && count > requirement.max) return false;
        }
    }

    return true;
}

function pluginSnapshotContains(
    snapshot: CreativeCanvasActionContext["availablePluginActionIds"],
    actionId: string,
): boolean {
    if (!snapshot) return true;
    const setLike = snapshot as ReadonlySet<string>;
    return typeof setLike.has === "function"
        ? setLike.has(actionId)
        : (snapshot as readonly string[]).includes(actionId);
}

/**
 * Returns actions appropriate for a canvas surface, selection and current runtime lock.
 * Agent/governance projection actions are excluded unless explicitly requested.
 */
export function getCreativeCanvasActions(context: CreativeCanvasActionContext): CreativeCanvasAction[] {
    if (context.sessionRunning) {
        return LOCAL_CREATIVE_CANVAS_ACTIONS.filter((action) => (
            action.availableWhileRunning && selectionMatches(action.selection, context)
        ));
    }

    return CREATIVE_CANVAS_ACTIONS.filter((action) => {
        if (!context.includeNonInteractive && (action.scope === "agent_only" || action.scope === "governance")) {
            return false;
        }
        if (!selectionMatches(action.selection, context)) return false;

        if (action.binding?.kind === "mediakit") {
            return context.pluginAvailable
                && (context.pluginGranted || context.allowPluginGrantRequest === true)
                && pluginSnapshotContains(context.availablePluginActionIds, action.binding.action);
        }

        return true;
    });
}
