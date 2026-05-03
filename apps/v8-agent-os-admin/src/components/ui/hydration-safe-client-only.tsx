"use client";

import { useEffect, useState, type ReactNode } from "react";

type HydrationSafeClientOnlyProps = {
    children: ReactNode;
    fallback?: ReactNode;
};

export function HydrationSafeClientOnly({ children, fallback = null }: HydrationSafeClientOnlyProps) {
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        const timer = window.setTimeout(() => setMounted(true), 0);
        return () => window.clearTimeout(timer);
    }, []);

    return mounted ? <>{children}</> : <>{fallback}</>;
}
