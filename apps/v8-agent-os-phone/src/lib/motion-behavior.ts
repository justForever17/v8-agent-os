export type HiddenControlBehavior = {
    pointerEvents: "auto" | "none";
    accessible: boolean;
    accessibilityElementsHidden: boolean;
    importantForAccessibility: "auto" | "no-hide-descendants";
    disabled: boolean;
};

export function resolveHiddenControlBehavior(isInteractive: boolean): HiddenControlBehavior {
    return {
        pointerEvents: isInteractive ? "auto" : "none",
        accessible: isInteractive,
        accessibilityElementsHidden: !isInteractive,
        importantForAccessibility: isInteractive ? "auto" : "no-hide-descendants",
        disabled: !isInteractive,
    };
}

export type TerminalScrollState = {
    contentHeight: number;
    viewportHeight: number;
    offsetY: number;
    isDragging: boolean;
    isPinnedToBottom: boolean;
};

export type TerminalScrollEvent =
    | { type: "viewport_resize"; viewportHeight: number }
    | { type: "content_resize"; contentHeight: number }
    | { type: "scroll"; contentHeight: number; viewportHeight: number; offsetY: number }
    | { type: "drag_start" }
    | { type: "drag_end" };

export type TerminalScrollTransition = {
    state: TerminalScrollState;
    shouldScrollToEnd: boolean;
};

export const TERMINAL_BOTTOM_THRESHOLD = 24;

function normalizeMetric(value: number) {
    return Number.isFinite(value) ? Math.max(0, value) : 0;
}

export function isTerminalViewportPinned(
    state: Pick<TerminalScrollState, "contentHeight" | "viewportHeight" | "offsetY">,
    threshold = TERMINAL_BOTTOM_THRESHOLD,
) {
    const contentHeight = normalizeMetric(state.contentHeight);
    const viewportHeight = normalizeMetric(state.viewportHeight);
    const offsetY = normalizeMetric(state.offsetY);
    const distanceFromBottom = Math.max(0, contentHeight - viewportHeight - offsetY);
    return distanceFromBottom <= Math.max(0, threshold);
}

export function createTerminalScrollState(): TerminalScrollState {
    return {
        contentHeight: 0,
        viewportHeight: 0,
        offsetY: 0,
        isDragging: false,
        isPinnedToBottom: true,
    };
}

export function reduceTerminalScrollState(
    current: TerminalScrollState,
    event: TerminalScrollEvent,
): TerminalScrollTransition {
    if (event.type === "drag_start" || event.type === "drag_end") {
        return {
            state: {
                ...current,
                isDragging: event.type === "drag_start",
            },
            shouldScrollToEnd: false,
        };
    }

    if (event.type === "viewport_resize") {
        const state = {
            ...current,
            viewportHeight: normalizeMetric(event.viewportHeight),
        };
        return {
            state: {
                ...state,
                isPinnedToBottom: isTerminalViewportPinned(state),
            },
            shouldScrollToEnd: false,
        };
    }

    if (event.type === "scroll") {
        const state = {
            ...current,
            contentHeight: normalizeMetric(event.contentHeight),
            viewportHeight: normalizeMetric(event.viewportHeight),
            offsetY: normalizeMetric(event.offsetY),
        };
        return {
            state: {
                ...state,
                isPinnedToBottom: isTerminalViewportPinned(state),
            },
            shouldScrollToEnd: false,
        };
    }

    const contentHeight = normalizeMetric(event.contentHeight);
    const shouldScrollToEnd = current.isPinnedToBottom && !current.isDragging;
    const state = {
        ...current,
        contentHeight,
    };
    return {
        state: {
            ...state,
            isPinnedToBottom: shouldScrollToEnd || isTerminalViewportPinned(state),
        },
        shouldScrollToEnd,
    };
}

type RequestFrame = (callback: () => void) => number;
type CancelFrame = (handle: number) => void;

export function createFrameTaskScheduler(requestFrame: RequestFrame, cancelFrame: CancelFrame) {
    let pendingHandle: number | null = null;
    let pendingTask: (() => void) | null = null;

    return {
        request(task: () => void) {
            pendingTask = task;
            if (pendingHandle !== null) {
                return false;
            }
            pendingHandle = requestFrame(() => {
                pendingHandle = null;
                const taskToRun = pendingTask;
                pendingTask = null;
                taskToRun?.();
            });
            return true;
        },
        cancel() {
            if (pendingHandle !== null) {
                cancelFrame(pendingHandle);
            }
            pendingHandle = null;
            pendingTask = null;
        },
        isPending() {
            return pendingHandle !== null;
        },
    };
}
