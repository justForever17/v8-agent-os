"use client";

import * as React from "react";
import Link from "next/link";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export interface SettingToggleCardProps {
    id?: string;
    title: React.ReactNode;
    description?: React.ReactNode;
    checked: boolean;
    disabled?: boolean;
    onCheckedChange: (checked: boolean) => void;
    className?: string;
    href?: string;
    showStatusDot?: boolean;
    statusDotEnabled?: boolean;
    statusLabel?: React.ReactNode;
    extraBadge?: React.ReactNode;
    titleClassName?: string;
}

export function SettingToggleCard({
    id,
    title,
    description,
    checked,
    disabled = false,
    onCheckedChange,
    className,
    href,
    showStatusDot = false,
    statusDotEnabled = false,
    statusLabel,
    extraBadge,
    titleClassName,
}: SettingToggleCardProps) {
    const generatedId = React.useId();
    const switchId = id || generatedId;

    const titleElement = href ? (
        <Link
            href={href}
            className={cn("block truncate text-sm font-semibold text-foreground transition hover:text-primary", titleClassName)}
        >
            {title}
        </Link>
    ) : (
        <Label
            htmlFor={switchId}
            className={cn("block cursor-pointer text-sm font-semibold text-foreground", titleClassName)}
        >
            {title}
        </Label>
    );

    return (
        <div
            data-v8-context-menu-ignore
            className={cn("flex select-none items-center justify-between gap-4 rounded-xl border border-border bg-muted/35 p-4 shadow-none transition-colors hover:bg-muted/60", className)}
        >
            <div className="min-w-0 space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                    {titleElement}
                    {extraBadge}
                </div>
                {description && (
                    <div className="text-xs leading-relaxed text-muted-foreground">
                        {description}
                    </div>
                )}
                {showStatusDot && (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span
                            className={cn(
                                "inline-flex h-2.5 w-2.5 shrink-0 rounded-full",
                                statusDotEnabled 
                                    ? "bg-emerald-500 shadow-[0_0_0_4px_rgba(16,185,129,0.12)]" 
                                    : "bg-rose-500 shadow-[0_0_0_4px_rgba(244,63,94,0.12)]",
                            )}
                            aria-hidden="true"
                        />
                        {statusLabel && <span>{statusLabel}</span>}
                    </div>
                )}
            </div>
            <Switch
                id={switchId}
                checked={checked}
                disabled={disabled}
                onCheckedChange={onCheckedChange}
                aria-label={typeof title === "string" ? `${title} toggle` : undefined}
                className={disabled ? "data-[state=checked]:bg-slate-300 data-[state=unchecked]:bg-slate-200" : undefined}
            />
        </div>
    );
}
