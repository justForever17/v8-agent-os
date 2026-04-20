import { Redirect } from "expo-router";

import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export default function DesktopLiveScreen() {
    const { status } = useAppSession();
    const { t } = useUiPrefs();

    if (status === "booting") {
        return <LoadingScreen label={t("src.screens.desktoplivescreen.returning_to_chat")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return <Redirect href="/chat" />;
}
