import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import { buildProductThemeBootstrapScript, PRODUCT_THEME_STORAGE_KEY } from "@v8/product-ui/theme-bootstrap";
import "./globals.css";
import "@v8/product-ui/styles.css";
import { LocaleProvider } from "@/components/providers/LocaleProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { AppContextMenu } from "@/components/ui/AppContextMenu";
import { LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";
import { warmDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveInitialProductTheme } from "@/lib/server/product-theme";

export const metadata: Metadata = {
  title: "V8 Agent OS",
  description: "Control plane for configuration, observability, and governance.",
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
  void warmDesktopLiveBridge().catch(() => undefined);

  return (
    <html lang={initialLocale} suppressHydrationWarning>
      <head>
        <script
          id="v8-product-theme-bootstrap"
          dangerouslySetInnerHTML={{
            __html: buildProductThemeBootstrapScript(initialTheme.theme, PRODUCT_THEME_STORAGE_KEY),
          }}
        />
      </head>
      <body className="font-sans antialiased" suppressHydrationWarning>
        <LocaleProvider initialLocale={initialLocale}>
          <ThemeProvider
            attribute="class"
            canonicalTheme={initialTheme.theme}
            defaultTheme={initialTheme.theme}
            enableSystem
            initialSyncState={initialTheme.syncState}
            storageKey={PRODUCT_THEME_STORAGE_KEY}
            disableTransitionOnChange
          >
            <AppContextMenu />
            <div className="min-h-screen flex flex-col">
              <main className="flex-1">{children}</main>
            </div>
          </ThemeProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
