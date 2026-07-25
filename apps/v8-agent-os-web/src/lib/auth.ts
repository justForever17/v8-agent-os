import NextAuth from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { authConfig } from "./auth.config";
import { shouldUseSecureCookies } from "./server/cookie-policy";

export const { handlers, signIn, signOut, auth } = NextAuth({
    ...authConfig,

    session: {
        strategy: "jwt",
        maxAge: 30 * 24 * 60 * 60, // 30 days
    },
    // Custom cookie to isolate Web session from Admin session
    cookies: {
        sessionToken: {
            name: `v8-agent-os-web.session-token`,
            options: {
                httpOnly: true,
                sameSite: 'lax',
                path: '/',
                secure: shouldUseSecureCookies(),
            },
        },
    },
    providers: [
        CredentialsProvider({
            name: "Local V8OS session",
            credentials: {
                adminBaseUrl: { label: "Admin URL", type: "text" },
                localSession: { label: "Local session", type: "text" },
            },
            async authorize(credentials) {
                const localSession = String(credentials?.localSession || "").trim() === "1";
                if (!localSession) return null;
                const adminBaseUrl = String(credentials?.adminBaseUrl || "http://127.0.0.1:9528").trim().replace(/\/+$/, "");
                if (!adminBaseUrl) return null;
                try {
                    const response = await fetch(`${adminBaseUrl}/api/client/auth/local-session`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            surface: "web",
                            deviceName: "v8-web-local",
                        }),
                    });
                    const payload = await response.json().catch(() => ({}));
                    return response.ok && payload?.user ? payload.user : null;
                } catch {
                    return null;
                }
            }
        })
    ],
    callbacks: {
        ...authConfig.callbacks,
        async jwt({ token, user, trigger, session }) {
            if (user) {
                token.id = user.id;
                token.login = user.login;
                token.role = typeof user.role === "string" ? user.role : token.role;
                token.email = user.email || token.email;
                token.name = user.name || token.name;
                token.picture = user.image || token.picture;
            }
            if (trigger === "update" && session) {
                if (typeof session.login === "string") token.login = session.login;
                if (typeof session.role === "string") token.role = session.role;
                if (typeof session.email === "string") token.email = session.email;
                if (typeof session.name === "string") token.name = session.name;
                if (typeof session.image === "string") token.picture = session.image;
            }
            return token;
        },
        async session({ session, token }) {
            if (token && session.user) {
                session.user.id = token.id as string;
                session.user.login = typeof token.login === "string" ? token.login : "";
                session.user.role = typeof token.role === "string" ? token.role : "";
                session.user.email = typeof token.email === "string" ? token.email : session.user.email;
                session.user.name = typeof token.name === "string" ? token.name : session.user.name;
                session.user.image = typeof token.picture === "string" ? token.picture : session.user.image;
            }
            return session;
        },
    },
});
