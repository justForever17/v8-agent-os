import NextAuth from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { authConfig } from "./auth.config";
import { resolveAdminApiBaseUrl, resolveInternalSecret } from "./server/runtime-config";

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
                secure: process.env.NODE_ENV === 'production',
            },
        },
    },
    providers: [
        CredentialsProvider({
            name: "Credentials",
            credentials: {
                login: { label: "Login", type: "text" },
                password: { label: "Password", type: "password" },
                pairingUri: { label: "Pairing link", type: "text" },
            },
            async authorize(credentials) {
                const pairingUri = String(credentials?.pairingUri || "").trim();
                if (pairingUri) {
                    try {
                        const parsed = new URL(pairingUri);
                        const adminBaseUrl = String(parsed.searchParams.get("admin") || "").trim().replace(/\/+$/, "");
                        const code = String(parsed.searchParams.get("code") || "").trim();
                        const instanceId = String(parsed.searchParams.get("instance") || "").trim();
                        if (!adminBaseUrl || !code) return null;
                        const response = await fetch(`${adminBaseUrl}/api/client/pairing/consume`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                code,
                                instanceId: instanceId || undefined,
                                sessionKind: "web_session",
                                deviceName: "v8-web",
                            }),
                        });
                        const payload = await response.json().catch(() => ({}));
                        return response.ok && payload?.user ? payload.user : null;
                    } catch {
                        return null;
                    }
                }
                const login = String(credentials?.login || "").trim();
                const password = String(credentials?.password || "");
                if (!login || !password) {
                    return null;
                }

                try {
                    const baseUrl = await resolveAdminApiBaseUrl();
                    const internalSecret = await resolveInternalSecret();

                    if (!internalSecret) {
                        console.error("Web 端未读取到内部服务密钥");
                        return null;
                    }

                    const res = await fetch(`${baseUrl}/auth/verify-credentials`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'x-v8-agent-os-secret': internalSecret
                        },
                        body: JSON.stringify({
                            login,
                            password
                        })
                    });

                    if (!res.ok) {
                        console.error("Auth verification failed:", await res.text());
                        return null;
                    }

                    const data = await res.json();

                    if (data.success && data.user) {
                        return data.user;
                    }

                } catch (error) {
                    console.error("Auth error:", error);
                }

                return null;
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
                token.mustChangePassword = Boolean(user.mustChangePassword);
            }
            if (trigger === "update" && session) {
                if (typeof session.login === "string") token.login = session.login;
                if (typeof session.role === "string") token.role = session.role;
                if (typeof session.email === "string") token.email = session.email;
                if (typeof session.name === "string") token.name = session.name;
                if (typeof session.image === "string") token.picture = session.image;
                if (typeof session.mustChangePassword === "boolean") token.mustChangePassword = session.mustChangePassword;
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
                session.user.mustChangePassword = Boolean(token.mustChangePassword);
            }
            return session;
        },
    },
});
