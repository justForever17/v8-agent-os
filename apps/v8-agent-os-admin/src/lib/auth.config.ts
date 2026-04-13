import type { NextAuthConfig } from "next-auth"

function shouldTrustCurrentHost() {
    if (process.env.AUTH_TRUST_HOST === "true") {
        return true;
    }

    const authUrl = String(process.env.AUTH_URL || process.env.NEXTAUTH_URL || "").trim();
    if (!authUrl) {
        return process.env.NODE_ENV !== "production";
    }

    return /https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?/i.test(authUrl);
}

export const authConfig = {
    trustHost: shouldTrustCurrentHost(),
    pages: {
        signIn: "/admin/login",
        verifyRequest: "/admin/verify",
    },
    callbacks: {
        authorized({ auth, request: { nextUrl } }) {
            const isLoggedIn = !!auth?.user
            const isOnAdmin = nextUrl.pathname.startsWith('/admin')
            const isOnLoginPage = nextUrl.pathname === '/admin/login'
            const isOnVerifyPage = nextUrl.pathname === '/admin/verify'

            // Allow access to login and verify pages
            if (isOnLoginPage || isOnVerifyPage) return true

            if (isOnAdmin) {
                if (!isLoggedIn) return false // Redirect to login

                if (auth.user?.role === 'ADMIN') {
                    return true
                }

                return false
            }

            return true
        },
        async session({ session, token }) {
            if (session.user) {
                if (token.sub) {
                    session.user.id = token.sub;
                }
                session.user.role = typeof token.role === "string" ? token.role : "ADMIN";
                session.user.login = typeof token.login === "string" ? token.login : "";
                session.user.mustChangePassword = Boolean(token.mustChangePassword);
                if (typeof token.email === "string") {
                    session.user.email = token.email;
                }
                if (typeof token.name === "string") {
                    session.user.name = token.name;
                }
                if (typeof token.picture === "string") {
                    session.user.image = token.picture;
                }
            }
            return session
        },
        async jwt({ token, user }) {
            if (user) {
                token.sub = user.id;
                token.role = user.role;
                token.login = user.login;
                token.mustChangePassword = user.mustChangePassword;
                token.picture = user.image || token.picture;
            }
            return token;
        }
    },
    providers: [], // Providers are configured in auth.ts
    // Custom cookie to isolate Admin session from Web session
    cookies: {
        sessionToken: {
            name: `v8-agent-os-admin.session-token`,
            options: {
                httpOnly: true,
                sameSite: 'lax',
                path: '/',
                secure: process.env.NODE_ENV === 'production',
            },
        },
    },
} satisfies NextAuthConfig
