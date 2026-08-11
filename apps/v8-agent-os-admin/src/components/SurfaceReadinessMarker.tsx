"use client";

import { useEffect, useRef } from "react";

export function SurfaceReadinessMarker() {
  const markerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    markerRef.current?.setAttribute("data-v8os-hydration", "ready");
  }, []);

  return (
    <span
      ref={markerRef}
      aria-hidden="true"
      className="hidden"
      data-v8os-hydration="pending"
      data-v8os-style-probe="true"
    />
  );
}
