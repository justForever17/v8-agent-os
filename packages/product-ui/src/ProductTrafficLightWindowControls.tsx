"use client";

export type ProductTrafficLightWindowControlsProps = {
  onClose: () => void;
  onMinimize: () => void;
  onToggleMaximize: () => void;
  closeLabel: string;
  minimizeLabel: string;
  maximizeLabel: string;
};

function TrafficLightGlyph({ kind }: { kind: "close" | "minimize" | "maximize" }) {
  return (
    <svg viewBox="0 0 10 10" aria-hidden="true" focusable="false">
      {kind === "close" ? (
        <>
          <path d="M2.6 2.6 7.4 7.4" />
          <path d="M7.4 2.6 2.6 7.4" />
        </>
      ) : null}
      {kind === "minimize" ? <path d="M2.3 5h5.4" /> : null}
      {kind === "maximize" ? (
        <>
          <path d="M5 2.3v5.4" />
          <path d="M2.3 5h5.4" />
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
}: ProductTrafficLightWindowControlsProps) {
  return (
    <div className="v8-product-traffic-lights" role="group" aria-label="Window controls">
      <button type="button" className="v8-product-traffic-light v8-product-traffic-light--close" onClick={onClose} aria-label={closeLabel} title={closeLabel}>
        <TrafficLightGlyph kind="close" />
      </button>
      <button type="button" className="v8-product-traffic-light v8-product-traffic-light--minimize" onClick={onMinimize} aria-label={minimizeLabel} title={minimizeLabel}>
        <TrafficLightGlyph kind="minimize" />
      </button>
      <button type="button" className="v8-product-traffic-light v8-product-traffic-light--maximize" onClick={onToggleMaximize} aria-label={maximizeLabel} title={maximizeLabel}>
        <TrafficLightGlyph kind="maximize" />
      </button>
    </div>
  );
}
