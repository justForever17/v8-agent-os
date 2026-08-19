import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import {
    buildProductThemeBootstrapScript,
    normalizeProductTheme,
    PRODUCT_THEME_COOKIE_KEY,
    PRODUCT_THEME_STORAGE_KEY,
} from "@v8/product-ui/theme-bootstrap";
import "./globals.css";
import "@v8/product-ui/styles.css";
import { Topbar } from "@/components/layout/Topbar";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { LocaleProvider } from "@/components/providers/LocaleProvider";
import { PersonalizationProvider } from "@/components/providers/PersonalizationProvider";
import { SurfaceReadinessMarker } from "@/components/SurfaceReadinessMarker";
import { AppContextMenu } from "@/components/ui/AppContextMenu";
import { auth } from "@/lib/auth";
import { LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";
import { buildPersonalizationBootstrapScript } from "@/lib/personalization";
import { resolveInitialProductTheme } from "@/lib/server/product-theme";


export const metadata: Metadata = {
    title: "V8 Agent OS - AI Assistant",
    description: "Agent operating system for chat, tasks, memory, and automation.",
    icons: {
        icon: "/icon.png",
        apple: "/apple-icon.png",
        shortcut: "/favicon.ico",
    },
};

export default async function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    const cookieStore = await cookies();
    const headerStore = await headers();
    const initialSession = await auth();
    const initialLocale = resolveInitialLocale(
        cookieStore.get(LOCALE_COOKIE_NAME)?.value,
        headerStore.get("accept-language"),
    );
    const themeCookie = cookieStore.get(PRODUCT_THEME_COOKIE_KEY)?.value;
    const initialTheme = await resolveInitialProductTheme(normalizeProductTheme(themeCookie));

    return (
        <html lang={initialLocale} suppressHydrationWarning className="h-full">
            <head>
                <script
                    id="v8-product-theme-bootstrap"
                    dangerouslySetInnerHTML={{
                        __html: buildProductThemeBootstrapScript(
                            initialTheme.theme,
                            PRODUCT_THEME_STORAGE_KEY,
                            initialTheme.syncState === "synced",
                        ),
                    }}
                />
                <script
                    id="v8-personalization-bootstrap"
                    dangerouslySetInnerHTML={{ __html: buildPersonalizationBootstrapScript() }}
                />
            </head>
            <body className="h-full overflow-hidden bg-background text-foreground antialiased font-sans" suppressHydrationWarning={true}>
                <LocaleProvider initialLocale={initialLocale}>
                    <SessionProvider session={initialSession}>
                        <ThemeProvider
                            attribute="class"
                            canonicalTheme={initialTheme.theme}
                            defaultTheme={initialTheme.theme}
                            enableSystem
                            initialSyncState={initialTheme.syncState}
                            storageKey={PRODUCT_THEME_STORAGE_KEY}
                            disableTransitionOnChange
                        >
                            <PersonalizationProvider>
                                <SurfaceReadinessMarker />
                                <AppContextMenu />
                                <div className="relative z-0 flex h-dvh min-h-dvh flex-col overflow-hidden">
                                    <Topbar />
                                    <main className="flex min-h-0 flex-1 overflow-hidden">
                                        {children}
                                    </main>
                                </div>
                            </PersonalizationProvider>
                        </ThemeProvider>
                    </SessionProvider>
                </LocaleProvider>
            </body>
        </html>
    );
}
