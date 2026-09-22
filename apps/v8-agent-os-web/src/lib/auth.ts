import NextAuth from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { authConfig } from "./auth.config";
import { shouldUseSecureCookies } from "./server/cookie-policy";
import { resolveEngineBaseUrl, resolveInternalSecret } from "./server/runtime-config";

export const { handlers, signIn, signOut, auth } = NextAuth({
    ...authConfig,

    session: {
        strategy: "jwt",
        maxAge: 30 * 24 * 60 * 60, // 30 days
    },
    // One host owns one session cookie. Admin pages use the same principal and
    // role claims instead of maintaining a second NextAuth session.
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
            name: "V8OS credentials",
            credentials: {
                adminBaseUrl: { label: "Admin URL", type: "text" },
                localSession: { label: "Local session", type: "text" },
                login: { label: "Login", type: "text" },
                password: { label: "Password", type: "password" },
            },
            async authorize(credentials) {
                const localSession = String(credentials?.localSession || "").trim() === "1";
                const engineBaseUrl = await resolveEngineBaseUrl();
                const internalSecret = await resolveInternalSecret();
                if (!internalSecret) return null;
                try {
                    if (!localSession) {
                        const login = String(credentials?.login || "").trim();
                        const password = String(credentials?.password || "");
                        if (!login || !password) return null;
                        const response = await fetch(`${engineBaseUrl}/client-identity/verify-credentials`, {
                            method: "POST",
                            cache: "no-store",
                            redirect: "error",
                            headers: {
                                "content-type": "application/json",
                                "x-v8-agent-os-secret": internalSecret,
                            },
                            body: JSON.stringify({ login, password }),
                        });
                        const payload = await response.json().catch(() => ({}));
                        if (!response.ok || !payload?.user || payload.user.role !== "ADMIN") return null;
                        return {
                            ...payload.user,
                            id: payload.user.id,
                            email: payload.user.email || payload.user.login,
                            login: payload.user.login,
                            role: "ADMIN",
                            mustChangePassword: Boolean(payload.user.mustChangePassword),
                            adminAuthenticated: true,
                        };
                    }
                    const response = await fetch(`${engineBaseUrl}/client-identity/local-session`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json", "x-v8-agent-os-secret": internalSecret },
                        body: JSON.stringify({
                            surface: "web",
                            deviceName: "v8-web-local",
                        }),
                    });
                    const payload = await response.json().catch(() => ({}));
                    return response.ok && payload?.user
                        ? { ...payload.user, adminAuthenticated: false }
                        : null;
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
                token.mustChangePassword = Boolean(user.mustChangePassword);
                token.adminAuthenticated = Boolean(user.adminAuthenticated);
                token.email = user.email || token.email;
                token.name = user.name || token.name;
                token.picture = user.image || token.picture;
            }
            if (trigger === "update" && session) {
                if (typeof session.login === "string") token.login = session.login;
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
                session.user.mustChangePassword = Boolean(token.mustChangePassword);
                session.user.adminAuthenticated = Boolean(token.adminAuthenticated);
                session.user.email = typeof token.email === "string" ? token.email : session.user.email;
                session.user.name = typeof token.name === "string" ? token.name : session.user.name;
                session.user.image = typeof token.picture === "string" ? token.picture : session.user.image;
            }
            return session;
        },
    },
});
