import { Redirect } from "expo-router";

import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { useAppSession } from "@/src/providers/app-session";

export default function DesktopLiveScreen() {
    const { status } = useAppSession();

    if (status === "booting") {
        return <LoadingScreen label="正在返回聊天主界面…" />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return <Redirect href="/chat" />;
}
