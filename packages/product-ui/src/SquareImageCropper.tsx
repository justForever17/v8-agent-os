"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

const MIN_ZOOM = 1;
const MAX_ZOOM = 3;
const PAN_STEP = 8;

type Size = { width: number; height: number };
type Offset = { x: number; y: number };

export type SquareImageCropperLabels = {
  cropArea: string;
  instruction: string;
  zoom: string;
  zoomOut: string;
  zoomIn: string;
  cancel: string;
  confirm: string;
};

export type SquareImageCropperProps = {
  file: File;
  labels: SquareImageCropperLabels;
  onCancel: () => void;
  onConfirm: (file: File) => void | Promise<void>;
  busy?: boolean;
  outputSize?: number;
};

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function cropGeometry(image: Size, viewport: Size, zoom: number) {
  if (!image.width || !image.height || !viewport.width || !viewport.height) {
    return { scale: 1, width: 0, height: 0, maxX: 0, maxY: 0 };
  }
  const baseScale = Math.max(viewport.width / image.width, viewport.height / image.height);
  const scale = baseScale * zoom;
  const width = image.width * scale;
  const height = image.height * scale;
  return {
    scale,
    width,
    height,
    maxX: Math.max(0, (width - viewport.width) / 2),
    maxY: Math.max(0, (height - viewport.height) / 2),
  };
}

function clampOffset(offset: Offset, geometry: ReturnType<typeof cropGeometry>): Offset {
  return {
    x: clamp(offset.x, -geometry.maxX, geometry.maxX),
    y: clamp(offset.y, -geometry.maxY, geometry.maxY),
  };
}

function canvasToBlob(canvas: HTMLCanvasElement) {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("Unable to encode cropped image")),
      "image/webp",
      0.9,
    );
  });
}

export function SquareImageCropper({
  file,
  labels,
  onCancel,
  onConfirm,
  busy = false,
  outputSize = 512,
}: SquareImageCropperProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const [source, setSource] = useState("");
  const [imageSize, setImageSize] = useState<Size>({ width: 0, height: 0 });
  const [viewportSize, setViewportSize] = useState<Size>({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [offset, setOffset] = useState<Offset>({ x: 0, y: 0 });
  const [encoding, setEncoding] = useState(false);

  const geometry = useMemo(
    () => cropGeometry(imageSize, viewportSize, zoom),
    [imageSize, viewportSize, zoom],
  );

  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setSource(objectUrl);
    setImageSize({ width: 0, height: 0 });
    setZoom(MIN_ZOOM);
    setOffset({ x: 0, y: 0 });
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const updateSize = () => {
      const rect = viewport.getBoundingClientRect();
      setViewportSize({ width: rect.width, height: rect.height });
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const handleWheel = (event: globalThis.WheelEvent) => {
      event.preventDefault();
      setZoom((current) => clamp(current + (event.deltaY > 0 ? -0.08 : 0.08), MIN_ZOOM, MAX_ZOOM));
    };
    viewport.addEventListener("wheel", handleWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", handleWheel);
  }, []);

  useEffect(() => {
    setOffset((current) => clampOffset(current, geometry));
  }, [geometry]);

  const updateZoom = useCallback((next: number) => {
    setZoom(clamp(next, MIN_ZOOM, MAX_ZOOM));
  }, []);

  const moveBy = useCallback((x: number, y: number) => {
    setOffset((current) => clampOffset({ x: current.x + x, y: current.y + y }, geometry));
  }, [geometry]);

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (busy || encoding) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    moveBy(deltaX, deltaY);
  };

  const handlePointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const movement: Record<string, Offset> = {
      ArrowLeft: { x: -PAN_STEP, y: 0 },
      ArrowRight: { x: PAN_STEP, y: 0 },
      ArrowUp: { x: 0, y: -PAN_STEP },
      ArrowDown: { x: 0, y: PAN_STEP },
    };
    if (movement[event.key]) {
      event.preventDefault();
      moveBy(movement[event.key].x, movement[event.key].y);
    }
  };

  const confirmCrop = async () => {
    const image = imageRef.current;
    if (!image || !imageSize.width || !viewportSize.width || encoding || busy) return;
    setEncoding(true);
    try {
      const sourceCropSize = viewportSize.width / geometry.scale;
      const originX = clamp(
        (imageSize.width - sourceCropSize) / 2 - offset.x / geometry.scale,
        0,
        imageSize.width - sourceCropSize,
      );
      const originY = clamp(
        (imageSize.height - sourceCropSize) / 2 - offset.y / geometry.scale,
        0,
        imageSize.height - sourceCropSize,
      );
      const canvas = document.createElement("canvas");
      canvas.width = outputSize;
      canvas.height = outputSize;
      const context = canvas.getContext("2d", { alpha: true });
      if (!context) throw new Error("Canvas is unavailable");
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      context.drawImage(
        image,
        originX,
        originY,
        sourceCropSize,
        sourceCropSize,
        0,
        0,
        outputSize,
        outputSize,
      );
      const blob = await canvasToBlob(canvas);
      const cropped = new File([blob], `avatar-crop-${Date.now()}.webp`, {
        type: blob.type || "image/webp",
        lastModified: Date.now(),
      });
      await onConfirm(cropped);
    } finally {
      setEncoding(false);
    }
  };

  const isBusy = busy || encoding;

  return (
    <div className="v8-square-cropper">
      <div
        ref={viewportRef}
        className="v8-square-cropper__viewport"
        role="application"
        aria-label={labels.cropArea}
        tabIndex={0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onKeyDown={handleKeyDown}
      >
        {source ? (
          <img
            ref={imageRef}
            src={source}
            alt=""
            draggable={false}
            className="v8-square-cropper__image"
            style={{
              left: `calc(50% + ${offset.x}px)`,
              top: `calc(50% + ${offset.y}px)`,
              width: geometry.width || undefined,
              height: geometry.height || undefined,
            }}
            onLoad={(event) => {
              setImageSize({
                width: event.currentTarget.naturalWidth,
                height: event.currentTarget.naturalHeight,
              });
            }}
          />
        ) : null}
        <div className="v8-square-cropper__mask" aria-hidden="true" />
      </div>

      <p className="v8-square-cropper__instruction">{labels.instruction}</p>
      <div className="v8-square-cropper__zoom-row">
        <span>{labels.zoom}</span>
        <button type="button" onClick={() => updateZoom(zoom - 0.1)} disabled={isBusy || zoom <= MIN_ZOOM} aria-label={labels.zoomOut}>−</button>
        <input
          type="range"
          min={MIN_ZOOM}
          max={MAX_ZOOM}
          step={0.01}
          value={zoom}
          onChange={(event) => updateZoom(Number(event.target.value))}
          aria-label={labels.zoom}
          disabled={isBusy}
        />
        <button type="button" onClick={() => updateZoom(zoom + 0.1)} disabled={isBusy || zoom >= MAX_ZOOM} aria-label={labels.zoomIn}>+</button>
      </div>
      <div className="v8-square-cropper__actions">
        <button type="button" className="v8-square-cropper__button v8-square-cropper__button--secondary" onClick={onCancel} disabled={isBusy}>
          {labels.cancel}
        </button>
        <button type="button" className="v8-square-cropper__button v8-square-cropper__button--primary" onClick={() => void confirmCrop()} disabled={isBusy || !imageSize.width}>
          {labels.confirm}
        </button>
      </div>
    </div>
  );
}
