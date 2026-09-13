"use client";

import type { Session } from "next-auth";
import { SessionProvider as NextAuthSessionProvider } from "next-auth/react";
import { ShellLifecycle } from "./ShellLifecycle";

export function SessionProvider({
    children,
    session,
}: {
    children: React.ReactNode;
    session: Session | null;
}) {
    return <NextAuthSessionProvider session={session}><ShellLifecycle>{children}</ShellLifecycle></NextAuthSessionProvider>;
}
