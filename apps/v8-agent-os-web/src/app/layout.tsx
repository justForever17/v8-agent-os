import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import "./globals.css";
import { Topbar } from "@/components/layout/Topbar";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { LocaleProvider } from "@/components/providers/LocaleProvider";
import { WebPasswordGate } from "@/components/auth/WebPasswordGate";
import { LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";


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

    return (
        <html lang={initialLocale} suppressHydrationWarning className="h-full">
            <body className="h-full overflow-hidden bg-background text-foreground antialiased font-sans" suppressHydrationWarning={true}>
                <LocaleProvider initialLocale={initialLocale}>
                    <SessionProvider>
                        <ThemeProvider
                            attribute="class"
                            defaultTheme="system"
                            enableSystem
                            disableTransitionOnChange
                        >
                            <div className="flex h-dvh min-h-dvh flex-col overflow-hidden">
                                <Topbar />
                                <main className="flex min-h-0 flex-1 overflow-hidden">
                                    {children}
                                </main>
                                <WebPasswordGate />
                            </div>
                        </ThemeProvider>
                    </SessionProvider>
                </LocaleProvider>
            </body>
        </html>
    );
}
