"use client";

import * as React from "react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type AdminSurfaceCardProps = React.ComponentPropsWithoutRef<typeof Card> & {
    surface?: "panel" | "nested";
};

const surfaceClassNameByVariant: Record<NonNullable<AdminSurfaceCardProps["surface"]>, string> = {
    panel: "rounded-2xl border-border bg-card text-card-foreground shadow-sm",
    nested: "rounded-2xl border-border bg-muted/40 text-card-foreground",
};

export const AdminSurfaceCard = React.forwardRef<HTMLDivElement, AdminSurfaceCardProps>(
    ({ className, surface = "panel", ...props }, ref) => (
        <Card
            ref={ref}
            className={cn(surfaceClassNameByVariant[surface], className)}
            {...props}
        />
    ),
);
AdminSurfaceCard.displayName = "AdminSurfaceCard";
