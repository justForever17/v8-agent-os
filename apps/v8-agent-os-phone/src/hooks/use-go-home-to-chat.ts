import { useCallback } from "react";
import { router, type Href } from "expo-router";

import { useAppSession } from "@/src/providers/app-session";

export function useGoHomeToChat() {
    const { setActiveConversationId } = useAppSession();

    return useCallback(async () => {
        await setActiveConversationId(null);
        router.replace("/chat" as Href);
    }, [setActiveConversationId]);
}
