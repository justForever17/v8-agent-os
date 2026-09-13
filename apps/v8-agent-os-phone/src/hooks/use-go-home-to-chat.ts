import { useCallback } from "react";
import { router, type Href } from "expo-router";

import { phoneDrafts } from "@/src/lib/phone-drafts";

export function useGoHomeToChat() {
    return useCallback(async () => {
        await phoneDrafts.flushAll();
        router.dismissTo("/chat" as Href);
    }, []);
}
