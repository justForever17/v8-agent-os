import type { NextAuthConfig } from "next-auth";

export const authConfig = {
    pages: {
        signIn: "/login",
        verifyRequest: "/admin/verify",
    },
    callbacks: {
        authorized({ auth, request: { nextUrl } }) {
            const pathname = nextUrl.pathname;
            if (pathname === "/login" || pathname === "/admin/verify") return true;
            if (pathname === "/admin" || pathname.startsWith("/admin/")) {
                return auth?.user?.role === "ADMIN" && auth.user.adminAuthenticated === true;
            }
            return true;
        },
        async jwt({ token, user }) {
            if (user) {
                token.id = user.id;
            }
            return token;
        },
        async session({ session, token }) {
            if (token && session.user) {
                session.user.id = token.id as string;
            }
            return session;
        },
    },
    providers: [],
} satisfies NextAuthConfig;
