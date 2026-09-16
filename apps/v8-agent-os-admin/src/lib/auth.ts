import NextAuth from "next-auth"
import CredentialsProvider from "next-auth/providers/credentials"
import { authConfig } from "./auth.config"
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config"

export const { handlers, signIn, signOut, auth } = NextAuth({
    ...authConfig,
    session: { strategy: "jwt" },
    providers: [
        CredentialsProvider({
            name: "Credentials",
            credentials: {
                login: { label: "Login", type: "text" },
                password: { label: "Password", type: "password" }
            },
            async authorize(credentials) {
                const login = String(credentials?.login || "").trim();
                const password = String(credentials?.password || "");
                if (!login || !password) return null;
                const secret = resolveInternalSecret();
                if (!secret) return null;
                const response = await fetch(`${resolveEngineBaseUrl()}/client-identity/verify-credentials`, {
                    method: "POST",
                    cache: "no-store",
                    redirect: "error",
                    headers: {
                        "content-type": "application/json",
                        "x-v8-agent-os-secret": secret,
                    },
                    body: JSON.stringify({ login, password }),
                }).catch(() => null);
                if (response?.ok) {
                    const user = await response.json().catch(() => null);
                    if (!user?.user || user.user.role !== "ADMIN") return null;
                    return {
                        id: user.user.id,
                        email: user.user.email || user.user.login,
                        login: user.user.login,
                        name: user.user.name,
                        image: user.user.image,
                        role: "ADMIN",
                        mustChangePassword: Boolean(user.user.mustChangePassword),
                    };
                }
                return null;
            }
        })
    ],
    callbacks: {
        async jwt({ token, user }) {
            if (user) {
                token.role = "ADMIN";
                token.login = user.login;
                token.picture = user.image || token.picture;
                token.mustChangePassword = user.mustChangePassword;
            }
            return token;
        },
        async session({ session, token }) {
            if (session.user) {
                if (token.sub) {
                    session.user.id = token.sub;
                }
                session.user.email = typeof token.email === "string" ? token.email : session.user.email;
                session.user.name = typeof token.name === "string" ? token.name : session.user.name;
                session.user.image = typeof token.picture === "string" ? token.picture : session.user.image;
                session.user.login = typeof token.login === "string" ? token.login : "";
                session.user.role = typeof token.role === "string" ? token.role : "ADMIN";
                session.user.mustChangePassword = Boolean(token.mustChangePassword);
            }
            return session;
        }
    }
})
