import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import "./globals.css";
import { LocaleProvider } from "@/components/providers/LocaleProvider";
import { LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";

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

  return (
    <html lang={initialLocale} suppressHydrationWarning>
      <body className="font-sans antialiased" suppressHydrationWarning>
        <LocaleProvider initialLocale={initialLocale}>
          <div className="min-h-screen flex flex-col">
            <main className="flex-1">{children}</main>
          </div>
        </LocaleProvider>
      </body>
    </html>
  );
}
