import { normalizeRuntimeArtifact, type RuntimeArtifact } from "@/lib/artifacts";
import type { RealtimeUiEvent } from "@/lib/chat-stream-state";
import {
    buildSessionStreamUiEvent,
    type NormalizedSessionRuntimeEvent,
} from "@v8/session-realtime";

export type LegacyChatEvent = RealtimeUiEvent & NormalizedSessionRuntimeEvent;

function buildArtifact(event: NormalizedSessionRuntimeEvent): RuntimeArtifact | undefined {
    const artifact = normalizeRuntimeArtifact(event.artifact || event.data?.artifact);
    return artifact || undefined;
}

export function normalizeRealtimeEvent(raw: unknown): LegacyChatEvent | null {
    return buildSessionStreamUiEvent(raw, {
        locale: "zh-CN",
        artifactResolver: (_artifact, event) => buildArtifact(event) || null,
    }) as LegacyChatEvent | null;
}
