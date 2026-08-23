export type WorkbenchAnimationFrameScheduler = {
    request: (callback: () => void) => number;
    cancel: (frameId: number) => void;
};

type WorkbenchResizeSessionOptions = {
    pointerId: number;
    parentRight: number;
    initialWidth: number;
    minimumWidth: number;
    maximumWidth: number;
    onPreview: (width: number) => void;
    onCommit: (width: number) => void;
    scheduler?: WorkbenchAnimationFrameScheduler;
};

export type CanvasRecorderLike = {
    state: string;
    stop: () => void;
};

type CanvasStreamLike = {
    getTracks: () => Array<{ stop: () => void }>;
};

type CanvasCaptureResources = {
    recorder: CanvasRecorderLike | null;
    stream: CanvasStreamLike | null;
    chunks: unknown[];
};

export type CanvasRecordingSession<TRecorder extends CanvasRecorderLike, TChunk> = {
    recorder: TRecorder;
    chunks: TChunk[];
    discarded: boolean;
};

type CanvasCaptureRequestState = {
    requestEpoch: number;
    currentEpoch: number;
    visible: boolean;
    requestSessionId: string;
    currentSessionId: string;
    sessionRunning: boolean;
};

function clampWidth(width: number, minimumWidth: number, maximumWidth: number) {
    const lowerBound = Math.min(minimumWidth, maximumWidth);
    const upperBound = Math.max(minimumWidth, maximumWidth);
    return Math.min(upperBound, Math.max(lowerBound, Math.round(width)));
}

export function createWorkbenchResizeSession({
    pointerId,
    parentRight,
    initialWidth,
    minimumWidth,
    maximumWidth,
    onPreview,
    onCommit,
    scheduler = {
        request: (callback) => window.requestAnimationFrame(callback),
        cancel: (frameId) => window.cancelAnimationFrame(frameId),
    },
}: WorkbenchResizeSessionOptions) {
    let active = true;
    let moved = false;
    let frameId: number | null = null;
    let pendingWidth = clampWidth(initialWidth, minimumWidth, maximumWidth);
    let previewedWidth: number | null = null;

    const publishPreview = () => {
        frameId = null;
        if (!active || previewedWidth === pendingWidth) return;
        previewedWidth = pendingWidth;
        onPreview(pendingWidth);
    };

    return {
        move(eventPointerId: number, clientX: number) {
            if (!active || eventPointerId !== pointerId) return false;
            moved = true;
            pendingWidth = clampWidth(parentRight - clientX, minimumWidth, maximumWidth);
            if (frameId === null) frameId = scheduler.request(publishPreview);
            return true;
        },
        finish(eventPointerId: number) {
            if (!active || eventPointerId !== pointerId) return false;
            active = false;
            if (frameId !== null) {
                scheduler.cancel(frameId);
                frameId = null;
            }
            if (moved) {
                if (previewedWidth !== pendingWidth) onPreview(pendingWidth);
                onCommit(pendingWidth);
            }
            return true;
        },
        dispose() {
            if (!active) return;
            active = false;
            if (frameId !== null) scheduler.cancel(frameId);
            frameId = null;
        },
    };
}

export function createCanvasRecordingSession<TRecorder extends CanvasRecorderLike, TChunk>(
    recorder: TRecorder,
): CanvasRecordingSession<TRecorder, TChunk> {
    return { recorder, chunks: [], discarded: false };
}

export function appendCanvasRecordingChunk<TRecorder extends CanvasRecorderLike, TChunk>(
    session: CanvasRecordingSession<TRecorder, TChunk>,
    chunk: TChunk,
) {
    if (session.discarded) return false;
    session.chunks.push(chunk);
    return true;
}

export function discardCanvasRecordingSession<TRecorder extends CanvasRecorderLike, TChunk>(
    session: CanvasRecordingSession<TRecorder, TChunk>,
) {
    session.discarded = true;
    session.chunks.splice(0, session.chunks.length);
}

export function consumeCanvasRecordingChunks<TRecorder extends CanvasRecorderLike, TChunk>(
    session: CanvasRecordingSession<TRecorder, TChunk>,
) {
    if (session.discarded) {
        session.chunks.splice(0, session.chunks.length);
        return [] as TChunk[];
    }
    return session.chunks.splice(0, session.chunks.length);
}

export function isCanvasCaptureRequestCurrent({
    requestEpoch,
    currentEpoch,
    visible,
    requestSessionId,
    currentSessionId,
    sessionRunning,
}: CanvasCaptureRequestState) {
    return requestEpoch === currentEpoch
        && visible
        && requestSessionId === currentSessionId
        && !sessionRunning;
}

export function releaseCanvasCapture({ recorder, stream, chunks }: CanvasCaptureResources) {
    chunks.splice(0, chunks.length);

    let recorderStopped = false;
    if (recorder && recorder.state !== "inactive") {
        try {
            recorder.stop();
            recorderStopped = true;
        } catch {
            // Cleanup is best effort; stream tracks still need to be released.
        }
    }

    let tracksStopped = 0;
    for (const track of stream?.getTracks() || []) {
        try {
            track.stop();
            tracksStopped += 1;
        } catch {
            // One failed track must not prevent the remaining tracks from stopping.
        }
    }

    return { recorderStopped, tracksStopped };
}
