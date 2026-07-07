"use client";

import type { ReactNode } from "react";

import { cx } from "./utils.js";

export type ProductSurfaceSwitcherItem = {
  id: string;
  label: ReactNode;
  href?: string;
  active?: boolean;
  disabled?: boolean;
  title?: string;
  onSelect?: () => void;
};

export type ProductSurfaceSwitcherProps = {
  items: ProductSurfaceSwitcherItem[];
  ariaLabel?: string;
  className?: string;
};

export function ProductSurfaceSwitcher({
  items,
  ariaLabel = "Product surface",
  className,
}: ProductSurfaceSwitcherProps) {
  return (
    <nav className={cx("v8-product-surface-switcher", className)} aria-label={ariaLabel}>
      {items.map((item) => {
        const className = cx(
          "v8-product-surface-switcher__item",
          item.active && "v8-product-surface-switcher__item--active",
          item.disabled && "v8-product-surface-switcher__item--disabled",
        );
        if (item.href && !item.disabled) {
          return (
            <a
              key={item.id}
              className={className}
              href={item.href}
              aria-current={item.active ? "page" : undefined}
              title={item.title}
              onClick={item.onSelect}
            >
              {item.label}
            </a>
          );
        }
        return (
          <button
            key={item.id}
            className={className}
            type="button"
            disabled={item.disabled || item.active}
            aria-current={item.active ? "page" : undefined}
            title={item.title}
            onClick={item.onSelect}
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
