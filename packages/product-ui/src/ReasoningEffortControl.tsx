"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MutableRefObject,
  type PointerEvent,
  type RefObject,
} from "react";

const PANEL_THUMB_INSET = 11.5;
const TRACK_THUMB_INSET = 9;
const FILL_DELAY_MS = 55;
const FILL_DURATION_MS = 980;
const FILL_HOLD_MS = 70;
const FILL_FADE_MS = 180;

export type ReasoningEffortControlVariant = "panel" | "track";

export type ReasoningEffortControlProps = {
  levels: readonly string[];
  value: string;
  onValueCommit?: (value: string) => void | Promise<void>;
  disabled?: boolean;
  variant?: ReasoningEffortControlVariant;
  label?: string;
  helpLabel?: string;
  ariaLabel?: string;
  labelFormatter?: (level: string) => string;
  className?: string;
};

export function resolveReasoningEffortStop(index: number, count: number): number {
  if (count <= 1) return 0;
  const safeIndex = Math.max(0, Math.min(count - 1, Math.round(index)));
  return safeIndex / (count - 1);
}

export function resolveNearestReasoningEffortIndex(position: number, count: number): number {
  if (count <= 1) return 0;
  const normalized = Math.max(0, Math.min(1, position));
  return Math.max(0, Math.min(count - 1, Math.round(normalized * (count - 1))));
}

function resolveReasoningEffortCssPosition(position: number, edgeInset: number): string {
  const normalized = Math.max(0, Math.min(1, position));
  const pixelOffset = edgeInset * (1 - normalized * 2);
  return `calc(${normalized * 100}% + ${pixelOffset}px)`;
}

function normalizeLevels(levels: readonly string[]): string[] {
  return Array.from(new Set(levels.map((level) => String(level || "").trim()).filter(Boolean)));
}

export type ReasoningEffortFillCell = {
  x: number;
  y: number;
  size: number;
  revealAt: number;
  alpha: number;
  tint: number;
};

function stableNoise(seed: number): number {
  let value = seed | 0;
  value = Math.imul(value ^ (value >>> 16), 0x45d9f3b);
  value = Math.imul(value ^ (value >>> 16), 0x45d9f3b);
  value ^= value >>> 16;
  return (value >>> 0) / 0xffffffff;
}

export function buildReasoningEffortFillCells(
  width: number,
  height: number,
  position: number,
  edgeInset = PANEL_THUMB_INSET,
): ReasoningEffortFillCell[] {
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);
  const widthScale = safeWidth / 312;
  const heightScale = safeHeight / 28;
  const pitch = Math.max(2.25, 3.35 * heightScale);
  const size = Math.max(1.35, pitch * 0.67);
  const scaledInset = Math.max(1, edgeInset * widthScale);
  const thumbCenter = scaledInset + Math.max(0, Math.min(1, position)) * Math.max(1, safeWidth - scaledInset * 2);
  const end = Math.max(2, Math.min(safeWidth - 2, thumbCenter - scaledInset * 0.72));
  const start = Math.max(2, end - safeWidth * 0.64);
  const span = Math.max(pitch, end - start);
  const rows = Math.max(1, Math.floor((safeHeight - pitch * 0.55) / pitch));
  const columns = Math.max(1, Math.floor(span / pitch));
  const cells: ReasoningEffortFillCell[] = [];

  for (let column = 0; column <= columns; column += 1) {
    const x = end - column * pitch;
    if (x < start) break;
    const distance = Math.max(0, Math.min(1, (end - x) / span));
    for (let row = 0; row < rows; row += 1) {
      const seed = (column + 1) * 73856093 ^ (row + 1) * 19349663;
      const occupancy = stableNoise(seed);
      if (occupancy < 0.07 + distance * 0.1) continue;
      const revealNoise = stableNoise(seed ^ 0x9e3779b9);
      const alphaNoise = stableNoise(seed ^ 0x85ebca6b);
      cells.push({
        x,
        y: pitch * (row + 0.72),
        size,
        revealAt: Math.max(0, Math.min(1.08, distance * 0.9 + (revealNoise - 0.5) * 0.22)),
        alpha: Math.max(0.22, Math.min(0.94, 0.9 - distance * 0.42 + alphaNoise * 0.22)),
        tint: stableNoise(seed ^ 0xc2b2ae35),
      });
    }
  }
  return cells;
}

function useReasoningFill({
  canvasRef,
  railRef,
  active,
  cycle,
  edgeInset,
  onComplete,
  positionRef,
}: {
  canvasRef: RefObject<HTMLCanvasElement | null>;
  railRef: RefObject<HTMLDivElement | null>;
  active: boolean;
  cycle: number;
  edgeInset: number;
  onComplete: () => void;
  positionRef: MutableRefObject<number>;
}) {
  useEffect(() => {
    const canvas = canvasRef.current;
    const rail = railRef.current;
    if (!canvas || !rail) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 1;
    let height = 1;
    let dpr = 1;
    let frameId = 0;
    let frameTimer = 0;
    let last = 0;
    let elapsedTotal = 0;
    let progress = 0;
    let fillOpacity = 1;
    let completed = false;
    const initialRect = rail.getBoundingClientRect();
    let inViewport = initialRect.bottom > 0
      && initialRect.right > 0
      && initialRect.top < window.innerHeight
      && initialRect.left < window.innerWidth;
    let pageVisible = document.visibilityState !== "hidden";
    let cells: ReasoningEffortFillCell[] = [];

    const draw = () => {
      context.clearRect(0, 0, width, height);
      if (!active) return;
      for (const cell of cells) {
        const local = Math.max(0, Math.min(1, (progress - cell.revealAt) / 0.12));
        if (local <= 0) continue;
        const settled = 1 - (1 - local) ** 3;
        const alpha = cell.alpha * settled * fillOpacity;
        const distance = Math.max(0, Math.min(1, (positionRef.current * width - cell.x) / Math.max(1, width * 0.64)));
        const hue = cell.tint > 0.72 ? 247 : 263;
        context.fillStyle = `hsla(${hue}, 100%, ${78 + (1 - distance) * 10}%, ${alpha})`;
        context.fillRect(cell.x, cell.y, cell.size, cell.size);
        if (alpha > 0.58 && distance < 0.35) {
          context.fillStyle = `rgba(225, 207, 255, ${alpha * 0.16})`;
          context.fillRect(cell.x - 0.35, cell.y - 0.35, cell.size + 0.7, cell.size + 0.7);
        }
      }
    };

    const layout = () => {
      const rect = rail.getBoundingClientRect();
      width = Math.max(1, rect.width);
      height = Math.max(1, rect.height);
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      cells = buildReasoningEffortFillCells(width, height, positionRef.current, edgeInset);
      draw();
    };

    const observer = new ResizeObserver(layout);
    observer.observe(rail);
    layout();

    if (!active) {
      context.clearRect(0, 0, width, height);
      return () => observer.disconnect();
    }

    const finish = () => {
      if (completed) return;
      completed = true;
      context.clearRect(0, 0, width, height);
      onComplete();
    };

    if (reduceMotion) {
      finish();
      return () => observer.disconnect();
    }

    const schedule = () => {
      if (!active || frameId || frameTimer || completed || !inViewport || !pageVisible) return;
      frameTimer = window.setTimeout(() => {
        frameTimer = 0;
        if (!active || completed || !inViewport || !pageVisible) return;
        frameId = requestAnimationFrame(step);
      }, 32);
    };

    const step = (now: number) => {
      frameId = 0;
      if (!last) last = now;
      const elapsed = Math.min(48, Math.max(0, now - last));
      last = now;
      elapsedTotal += elapsed;
      progress = Math.max(0, Math.min(1, (elapsedTotal - FILL_DELAY_MS) / FILL_DURATION_MS));
      const fadeStart = FILL_DELAY_MS + FILL_DURATION_MS + FILL_HOLD_MS;
      fillOpacity = 1 - Math.max(0, Math.min(1, (elapsedTotal - fadeStart) / FILL_FADE_MS));
      draw();
      if (elapsedTotal >= fadeStart + FILL_FADE_MS) {
        finish();
        return;
      }
      schedule();
    };

    const handleVisibility = () => {
      pageVisible = document.visibilityState !== "hidden";
      last = 0;
      schedule();
    };
    document.addEventListener("visibilitychange", handleVisibility);

    const intersectionObserver = typeof IntersectionObserver === "undefined"
      ? null
      : new IntersectionObserver(([entry]) => {
        inViewport = Boolean(entry?.isIntersecting);
        last = 0;
        schedule();
      }, { threshold: 0.01 });
    intersectionObserver?.observe(rail);
    draw();
    schedule();
    return () => {
      observer.disconnect();
      intersectionObserver?.disconnect();
      document.removeEventListener("visibilitychange", handleVisibility);
      window.clearTimeout(frameTimer);
      cancelAnimationFrame(frameId);
    };
  }, [active, canvasRef, cycle, edgeInset, onComplete, positionRef, railRef]);
}

export function ReasoningEffortControl({
  levels,
  value,
  onValueCommit,
  disabled = false,
  variant = "panel",
  label = "Effort",
  helpLabel,
  ariaLabel = label,
  labelFormatter = (level) => level,
  className = "",
}: ReasoningEffortControlProps) {
  const normalizedLevels = useMemo(() => normalizeLevels(levels), [levels]);
  const effectiveLevels = normalizedLevels.length ? normalizedLevels : [String(value || "auto")];
  const controlledIndex = Math.max(0, effectiveLevels.indexOf(value));
  const [draftIndex, setDraftIndex] = useState(controlledIndex);
  const [position, setPosition] = useState(() => resolveReasoningEffortStop(controlledIndex, effectiveLevels.length));
  const [dragging, setDragging] = useState(false);
  const [pendingValue, setPendingValue] = useState<string | null>(null);
  const [celebrationCycle, setCelebrationCycle] = useState(0);
  const [celebrating, setCelebrating] = useState(false);
  const pointerIdRef = useRef<number | null>(null);
  const committedIndexRef = useRef(controlledIndex);
  const pendingValueRef = useRef<string | null>(null);
  const valueRef = useRef(value);
  const positionRef = useRef(position);
  const railRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const edgeInset = variant === "track" ? TRACK_THUMB_INSET : PANEL_THUMB_INSET;
  const isMax = effectiveLevels.length > 1 && draftIndex === effectiveLevels.length - 1;
  const settledIsMax = isMax && !dragging;
  const handleCelebrationComplete = useCallback(() => setCelebrating(false), []);
  useReasoningFill({
    canvasRef,
    railRef,
    active: celebrating,
    cycle: celebrationCycle,
    edgeInset,
    onComplete: handleCelebrationComplete,
    positionRef,
  });

  useEffect(() => {
    valueRef.current = value;
  }, [value]);

  useEffect(() => {
    if (pendingValue) {
      if (value === pendingValue) {
        pendingValueRef.current = null;
        setPendingValue(null);
        committedIndexRef.current = controlledIndex;
      }
      return;
    }
    if (dragging) return;
    setDraftIndex(controlledIndex);
    committedIndexRef.current = controlledIndex;
    const next = resolveReasoningEffortStop(controlledIndex, effectiveLevels.length);
    positionRef.current = next;
    setPosition(next);
  }, [controlledIndex, dragging, effectiveLevels.length, pendingValue, value]);

  const setContinuousPosition = (next: number) => {
    const clamped = Math.max(0, Math.min(1, next));
    const nearest = resolveNearestReasoningEffortIndex(clamped, effectiveLevels.length);
    positionRef.current = clamped;
    setPosition(clamped);
    setDraftIndex(nearest);
  };

  const positionForPointer = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const usableWidth = Math.max(1, rect.width - edgeInset * 2);
    return rect.width > 0 ? (event.clientX - rect.left - edgeInset) / usableWidth : 0;
  };

  const commitIndex = (index: number) => {
    const safeIndex = Math.max(0, Math.min(effectiveLevels.length - 1, index));
    const snapped = resolveReasoningEffortStop(safeIndex, effectiveLevels.length);
    positionRef.current = snapped;
    setPosition(snapped);
    setDraftIndex(safeIndex);
    const previousCommittedIndex = committedIndexRef.current;
    committedIndexRef.current = safeIndex;
    if (safeIndex === effectiveLevels.length - 1 && previousCommittedIndex !== safeIndex) {
      setCelebrationCycle((current) => current + 1);
      setCelebrating(true);
    } else if (safeIndex !== effectiveLevels.length - 1) {
      setCelebrating(false);
    }
    const nextValue = effectiveLevels[safeIndex];
    if (nextValue && nextValue !== value) {
      pendingValueRef.current = nextValue;
      setPendingValue(nextValue);
      const result = onValueCommit?.(nextValue);
      void Promise.resolve(result).then(() => {
        window.setTimeout(() => {
          if (pendingValueRef.current === nextValue && valueRef.current !== nextValue) {
            pendingValueRef.current = null;
            setPendingValue(null);
          }
        }, 250);
      }).catch(() => {
        if (pendingValueRef.current === nextValue) {
          pendingValueRef.current = null;
          setPendingValue(null);
        }
      });
    }
  };

  const finishPointer = (event?: PointerEvent<HTMLDivElement>) => {
    if (event && pointerIdRef.current === event.pointerId) {
      setContinuousPosition(positionForPointer(event));
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }
    pointerIdRef.current = null;
    commitIndex(resolveNearestReasoningEffortIndex(positionRef.current, effectiveLevels.length));
    setDragging(false);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    let nextIndex = draftIndex;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") nextIndex -= 1;
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") nextIndex += 1;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = effectiveLevels.length - 1;
    else return;
    event.preventDefault();
    commitIndex(nextIndex);
  };

  const currentLevel = effectiveLevels[draftIndex] || effectiveLevels[0] || value;
  const currentLabel = labelFormatter(currentLevel);
  const rootStyle = {
    "--v8-reasoning-position": resolveReasoningEffortCssPosition(position, edgeInset),
  } as CSSProperties;

  const rail = (
    <div
      ref={railRef}
      className="v8-reasoning-effort__rail"
      onPointerDown={(event) => {
        if (disabled) return;
        event.preventDefault();
        pointerIdRef.current = event.pointerId;
        event.currentTarget.setPointerCapture(event.pointerId);
        setCelebrating(false);
        setDragging(true);
        setContinuousPosition(positionForPointer(event));
      }}
      onPointerMove={(event) => {
        if (disabled || pointerIdRef.current !== event.pointerId) return;
        setContinuousPosition(positionForPointer(event));
      }}
      onPointerUp={(event) => finishPointer(event)}
      onPointerCancel={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
        finishPointer();
      }}
    >
      <canvas ref={canvasRef} className="v8-reasoning-effort__fill" aria-hidden="true" />
      <span className="v8-reasoning-effort__ticks" aria-hidden="true">
        {effectiveLevels.map((level, index) => (
          <i
            key={`${level}:${index}`}
            style={{ left: resolveReasoningEffortCssPosition(resolveReasoningEffortStop(index, effectiveLevels.length), edgeInset) }}
          />
        ))}
      </span>
      <span className="v8-reasoning-effort__rail-label" aria-hidden="true">{currentLabel}</span>
      <button
        type="button"
        role="slider"
        className="v8-reasoning-effort__thumb"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={Math.max(0, effectiveLevels.length - 1)}
        aria-valuenow={draftIndex}
        aria-valuetext={currentLabel}
        onKeyDown={handleKeyDown}
      />
    </div>
  );

  return (
    <div
      className={`v8-reasoning-effort v8-reasoning-effort--${variant}${className ? ` ${className}` : ""}`}
      data-max={settledIsMax ? "true" : "false"}
      data-celebrating={celebrating ? "true" : "false"}
      data-dragging={dragging ? "true" : "false"}
      data-level={currentLevel}
      style={rootStyle}
    >
      {variant === "track" ? rail : (
        <>
          <div className="v8-reasoning-effort__head">
            <div className="v8-reasoning-effort__label">
              <span>{label}</span>
              <span className="v8-reasoning-effort__value">
                <span className="v8-reasoning-effort__normal-label">{currentLabel}</span>
                <span className="v8-reasoning-effort__max-label" aria-hidden={!isMax}>{currentLabel}</span>
              </span>
            </div>
            <span className="v8-reasoning-effort__help" title={helpLabel || ariaLabel} aria-hidden="true">?</span>
          </div>
          {rail}
        </>
      )}
    </div>
  );
}
