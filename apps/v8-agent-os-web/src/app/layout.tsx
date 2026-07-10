import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import { buildProductThemeBootstrapScript, PRODUCT_THEME_STORAGE_KEY } from "@v8/product-ui/theme-bootstrap";
import "./globals.css";
import "@v8/product-ui/styles.css";
import { Topbar } from "@/components/layout/Topbar";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { LocaleProvider } from "@/components/providers/LocaleProvider";
import { LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";
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
    const initialLocale = resolveInitialLocale(
        cookieStore.get(LOCALE_COOKIE_NAME)?.value,
        headerStore.get("accept-language"),
    );
    const initialTheme = await resolveInitialProductTheme();

    return (
        <html lang={initialLocale} suppressHydrationWarning className="h-full">
            <head>
                <script
                    id="v8-product-theme-bootstrap"
                    dangerouslySetInnerHTML={{
                        __html: buildProductThemeBootstrapScript(initialTheme.theme, PRODUCT_THEME_STORAGE_KEY),
                    }}
                />
            </head>
            <body className="h-full overflow-hidden bg-background text-foreground antialiased font-sans" suppressHydrationWarning={true}>
                <LocaleProvider initialLocale={initialLocale}>
                    <SessionProvider>
                        <ThemeProvider
                            attribute="class"
                            canonicalTheme={initialTheme.theme}
                            defaultTheme={initialTheme.theme}
                            enableSystem
                            initialSyncState={initialTheme.syncState}
                            storageKey={PRODUCT_THEME_STORAGE_KEY}
                            disableTransitionOnChange
                        >
                            <div className="flex h-dvh min-h-dvh flex-col overflow-hidden">
                                <Topbar />
                                <main className="flex min-h-0 flex-1 overflow-hidden">
                                    {children}
                                </main>
                            </div>
                        </ThemeProvider>
                    </SessionProvider>
                </LocaleProvider>
            </body>
        </html>
    );
}
