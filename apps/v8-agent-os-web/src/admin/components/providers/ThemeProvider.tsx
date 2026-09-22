"use client";

import * as React from "react";
import { ProductThemeProvider } from "@v8/product-ui";

export function ThemeProvider({
    children,
    ...props
}: React.ComponentProps<typeof ProductThemeProvider>) {
    return <ProductThemeProvider {...props}>{children}</ProductThemeProvider>;
}
