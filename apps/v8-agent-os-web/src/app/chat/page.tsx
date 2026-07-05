import { Suspense } from "react";

import { ChatPageFallback } from "./ChatPageFallback";
import ChatClient from "./ChatClient";

export const dynamic = 'force-dynamic';

export default async function ChatPage({
    searchParams,
}: {
    searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
    const query = new URLSearchParams();
    const params = await searchParams;
    for (const [key, value] of Object.entries(params || {})) {
        if (typeof value === "string") {
            query.set(key, value);
        }
    }
    return (
        <Suspense fallback={<ChatPageFallback />}>
            <ChatClient />
        </Suspense>
    );
}
