"use client";

export type ProductTrafficLightWindowControlsProps = {
  onClose: () => void;
  onMinimize: () => void;
  onToggleMaximize: () => void;
  closeLabel: string;
  minimizeLabel: string;
  maximizeLabel: string;
  restoreLabel?: string;
  isMaximized?: boolean;
};

function TrafficLightGlyph({ kind }: { kind: "close" | "minimize" | "maximize" | "restore" }) {
  return (
    <svg viewBox="0 0 10 10" aria-hidden="true" focusable="false">
      {kind === "close" ? (
        <>
          <path d="M2.6 2.6 7.4 7.4" />
          <path d="M7.4 2.6 2.6 7.4" />
        </>
      ) : null}
      {kind === "minimize" ? <path d="M2.3 5h5.4" /> : null}
      {kind === "maximize" ? <rect x="2.35" y="2.35" width="5.3" height="5.3" rx="0.7" /> : null}
      {kind === "restore" ? (
        <>
          <path d="M3.45 3.25V2.7c0-.52.42-.95.95-.95h2.9c.52 0 .95.43.95.95v2.9c0 .53-.43.95-.95.95h-.55" />
          <rect x="1.75" y="3.45" width="4.8" height="4.8" rx="0.75" />
        </>
      ) : null}
    </svg>
  );
}

export function ProductTrafficLightWindowControls({
  onClose,
  onMinimize,
  onToggleMaximize,
  closeLabel,
  minimizeLabel,
  maximizeLabel,
  restoreLabel,
  isMaximized = false,
}: ProductTrafficLightWindowControlsProps) {
  const maximizeActionLabel = isMaximized ? (restoreLabel || maximizeLabel) : maximizeLabel;
  return (
    <div className="v8-product-traffic-lights" role="group" aria-label="Window controls">
      <button type="button" className="v8-product-traffic-light v8-product-traffic-light--minimize" onClick={onMinimize} aria-label={minimizeLabel} title={minimizeLabel}>
        <TrafficLightGlyph kind="minimize" />
      </button>
      <button type="button" className="v8-product-traffic-light v8-product-traffic-light--maximize" onClick={onToggleMaximize} aria-label={maximizeActionLabel} title={maximizeActionLabel}>
        <TrafficLightGlyph kind={isMaximized ? "restore" : "maximize"} />
      </button>
      <button type="button" className="v8-product-traffic-light v8-product-traffic-light--close" onClick={onClose} aria-label={closeLabel} title={closeLabel}>
        <TrafficLightGlyph kind="close" />
      </button>
    </div>
  );
}
