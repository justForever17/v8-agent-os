export type ContinuousMotionContext = {
    reducedMotion: boolean;
    executionActive: boolean;
    surfaceVisible?: boolean;
    appVisible?: boolean;
};

export function shouldRunContinuousMotion({
    reducedMotion,
    executionActive,
    surfaceVisible = true,
    appVisible = true,
}: ContinuousMotionContext) {
    return !reducedMotion && executionActive && surfaceVisible && appVisible;
}

export function shouldRunTransitionMotion({
    reducedMotion,
    surfaceVisible = true,
    appVisible = true,
}: Omit<ContinuousMotionContext, "executionActive">) {
    return !reducedMotion && surfaceVisible && appVisible;
}

export function nextMotionFrameIndex(current: number, frameCount: number, loops: boolean) {
    if (frameCount <= 1) return null;
    if (current < frameCount - 1) return current + 1;
    return loops ? 0 : null;
}

export function retainDepartingMotionItem<T extends { id: string; renderPhase: string }>(
    stage: T,
    incomingIds: ReadonlySet<string>,
    startExit: (stage: T) => T,
) {
    if (incomingIds.has(stage.id)) return null;
    return stage.renderPhase === "exiting" ? stage : startExit(stage);
}

export function voiceWavePhase(index: number, barCount = 8) {
    if (barCount <= 0) return 0;
    return ((index % barCount) + barCount) % barCount / barCount;
}
