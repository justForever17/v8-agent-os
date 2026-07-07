"use client";

import type { ReactNode } from "react";

import { ProductWordmark } from "./ProductWordmark.js";
import { cx } from "./utils.js";

export type ProductTopbarProps = {
  brandImageSrc: string;
  brandLabel?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  windowControls?: ReactNode;
  className?: string;
};

export type ProductShellTopbarProps = ProductTopbarProps & {
  dragRegion?: boolean;
  shellClassName?: string;
};

function ProductTopbarContent({
  brandImageSrc,
  brandLabel = "V8 Agent OS",
  title,
  subtitle,
  actions,
  windowControls,
}: ProductTopbarProps) {
  return (
    <div className="v8-product-topbar__inner">
      <div className="v8-product-topbar__left">
        <div className="v8-product-brand" aria-label={brandLabel}>
          <img
            className="v8-product-brand__mark"
            src={brandImageSrc}
            alt={brandLabel}
            width={25}
            height={25}
            draggable={false}
            translate="no"
          />
          <ProductWordmark label={brandLabel} text="V8 Agent OS" />
        </div>
        {title || subtitle ? (
          <>
            <span className="v8-product-topbar__divider" aria-hidden="true">/</span>
            <div className="v8-product-topbar__title-wrap">
              {title ? <div className="v8-product-topbar__title">{title}</div> : null}
              {subtitle ? <div className="v8-product-topbar__subtitle">{subtitle}</div> : null}
            </div>
          </>
        ) : null}
      </div>
      <div className="v8-product-topbar__actions">
        {actions}
        {windowControls ? (
          <div className="v8-product-topbar__window-controls">
            {windowControls}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ProductTopbar(props: ProductTopbarProps) {
  return (
    <header className={cx("v8-product-topbar", props.className)}>
      <ProductTopbarContent {...props} />
    </header>
  );
}

export function ProductShellTopbar({
  dragRegion = true,
  shellClassName,
  ...props
}: ProductShellTopbarProps) {
  return (
    <header
      className={cx(
        "v8-product-topbar",
        "v8-product-shell-topbar",
        dragRegion && "v8-product-shell-topbar--drag",
        shellClassName,
        props.className,
      )}
    >
      <ProductTopbarContent {...props} />
    </header>
  );
}
