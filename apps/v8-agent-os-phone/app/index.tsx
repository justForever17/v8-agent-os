import { Redirect, type Href } from "expo-router";

import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { useAppSession } from "@/src/providers/app-session";

export default function IndexScreen() {
    const { status, user } = useAppSession();

    if (status === "booting") {
        return <LoadingScreen />;
    }

    if (status === "authenticated") {
        return <Redirect href={(user?.mustChangePassword ? "/settings" : "/chat") as Href} />;
    }

    return <Redirect href="/login" />;
}
