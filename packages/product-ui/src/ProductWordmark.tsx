"use client";

import { cx } from "./utils.js";

export type ProductWordmarkVariant = "graphite";

export type ProductWordmarkProps = {
  label?: string;
  text?: string;
  variant?: ProductWordmarkVariant;
  className?: string;
};

export function ProductWordmark({
  label = "V8 Agent OS",
  text = "V8 Agent OS",
  variant = "graphite",
  className,
}: ProductWordmarkProps) {
  return (
    <span
      className={cx("v8-product-wordmark", `v8-product-wordmark--${variant}`, className)}
      data-text={text}
      aria-label={label}
      translate="no"
    >
      <span aria-hidden="true">{text}</span>
    </span>
  );
}
