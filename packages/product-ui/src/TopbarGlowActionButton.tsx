"use client";

import * as React from "react";

import { cx } from "./utils.js";

export type TopbarGlowTone =
  | "amber"
  | "blue"
  | "cyan"
  | "emerald"
  | "fuchsia"
  | "rose"
  | "sky"
  | "slate"
  | "violet";

type AsChildProps = {
  className?: string;
  children?: React.ReactNode;
};

export type TopbarGlowActionButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: boolean;
  tone?: TopbarGlowTone;
  className?: string;
  children: React.ReactNode;
};

export const TopbarGlowActionButton = React.forwardRef<HTMLElement, TopbarGlowActionButtonProps>(
  ({ asChild = false, tone = "slate", className, children, type = "button", ...props }, ref) => {
    const controlClassName = cx(
      "v8-topbar-glow-action__control",
      `v8-topbar-glow-action__control--${tone}`,
      className,
    );

    let control: React.ReactNode;
    if (asChild && React.isValidElement<AsChildProps>(children)) {
      control = React.cloneElement(children, {
        ...props,
        className: cx(controlClassName, children.props.className),
        ref,
      } as Partial<AsChildProps> & { ref: React.Ref<HTMLElement> });
    } else {
      control = (
        <button
          ref={ref as React.Ref<HTMLButtonElement>}
          type={type}
          className={controlClassName}
          {...props}
        >
          {children}
        </button>
      );
    }

    return (
      <span className={cx("v8-topbar-glow-action", `v8-topbar-glow-action--${tone}`)}>
        <span className="v8-topbar-glow-action__glow" aria-hidden="true" />
        {control}
      </span>
    );
  },
);

TopbarGlowActionButton.displayName = "TopbarGlowActionButton";
