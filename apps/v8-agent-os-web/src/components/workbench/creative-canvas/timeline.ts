import type { CanvasTimeRange } from "./types";

export function isValidCanvasTimeRange(range: CanvasTimeRange | undefined) {
    return Boolean(
        range
        && range.probeFingerprint
        && range.exact !== false
        && (range.unit === "frame" || range.unit === "sample")
        && Number.isInteger(range.startIndex)
        && Number.isInteger(range.endIndexExclusive)
        && range.startIndex >= 0
        && range.endIndexExclusive > range.startIndex
        && range.endIndexExclusive <= range.count
        && (range.unit !== "frame" || range.boundaryTicks?.length === range.count + 1),
    );
}

export function isValidCanvasFramePick(range: CanvasTimeRange | undefined) {
    return Boolean(
        range
        && range.probeFingerprint
        && range.exact !== false
        && range.unit === "frame"
        && Number.isInteger(range.startIndex)
        && range.startIndex >= 0
        && range.startIndex < range.count
        && range.boundaryTicks?.length === range.count + 1,
    );
}

export function canvasTimelineSeconds(range: CanvasTimeRange, index: number) {
    if (range.unit === "frame") {
        const ticks = range.boundaryTicks?.[Math.max(0, Math.min(index, range.count))];
        if (Number.isFinite(ticks)) {
            return Number(ticks) * range.timeBaseNumerator / Math.max(1, range.timeBaseDenominator);
        }
        const rateNumerator = range.averageFrameRateNumerator || 0;
        const rateDenominator = range.averageFrameRateDenominator || 1;
        if (rateNumerator > 0) return index * rateDenominator / rateNumerator;
        return range.count > 0 ? index * (Number(range.durationSeconds) || 0) / range.count : 0;
    }
    return index * range.timeBaseNumerator / Math.max(1, range.timeBaseDenominator);
}

export function formatCanvasTimelineSeconds(range: CanvasTimeRange, index: number) {
    return `${canvasTimelineSeconds(range, index).toFixed(range.displayPrecision).replace(/0+$/, "").replace(/\.$/, "")}s`;
}

export function canvasTimelineIndexAtSeconds(range: CanvasTimeRange, seconds: number) {
    if (range.unit === "sample") {
        return Math.max(0, Math.min(
            range.count,
            Math.round(seconds * range.timeBaseDenominator / Math.max(1, range.timeBaseNumerator)),
        ));
    }
    const boundaries = range.boundaryTicks || [];
    if (!boundaries.length) {
        const rateNumerator = range.averageFrameRateNumerator || 0;
        const rateDenominator = range.averageFrameRateDenominator || 1;
        const index = rateNumerator > 0
            ? Math.round(seconds * rateNumerator / rateDenominator)
            : Math.round(seconds * range.count / Math.max(Number(range.durationSeconds) || 0, 0.001));
        return Math.max(0, Math.min(range.count, index));
    }
    let low = 0;
    let high = Math.max(0, boundaries.length - 1);
    const targetTicks = seconds * range.timeBaseDenominator / Math.max(1, range.timeBaseNumerator);
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if ((boundaries[middle] || 0) < targetTicks) low = middle + 1;
        else high = middle;
    }
    const previous = Math.max(0, low - 1);
    return Math.abs((boundaries[low] || 0) - targetTicks) < Math.abs((boundaries[previous] || 0) - targetTicks)
        ? low
        : previous;
}

export function reconcileCanvasTimeRange(
    previous: CanvasTimeRange,
    nextRange: CanvasTimeRange,
    mode: "frame" | "range",
) {
    const count = nextRange.count;
    if (previous.count <= 0) {
        const startIndex = Math.max(0, Math.min(Math.trunc(previous.startIndex) || 0, count - 1));
        const requestedEnd = Math.trunc(previous.endIndexExclusive) || count;
        return {
            ...nextRange,
            startIndex,
            endIndexExclusive: mode === "frame"
                ? startIndex + 1
                : Math.max(startIndex + 1, Math.min(requestedEnd, count)),
        };
    }

    const previousStartSeconds = canvasTimelineSeconds(previous, previous.startIndex);
    const previousEndSeconds = canvasTimelineSeconds(
        previous,
        mode === "frame" ? previous.startIndex + 1 : previous.endIndexExclusive,
    );
    const startIndex = Math.max(0, Math.min(canvasTimelineIndexAtSeconds(nextRange, previousStartSeconds), count - 1));
    return {
        ...nextRange,
        startIndex,
        endIndexExclusive: mode === "frame"
            ? startIndex + 1
            : Math.max(
                startIndex + 1,
                Math.min(canvasTimelineIndexAtSeconds(nextRange, previousEndSeconds), count),
            ),
    };
}
