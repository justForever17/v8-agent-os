import { Redirect, router, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";

import { RpaPanelContent } from "@/src/components/rpa/RpaPanelContent";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export default function RPAScreen() {
    const { status, userAvatarUri } = useAppSession();
    const { colors, toggleThemeMode } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const actions: PhoneTopbarAction[] = [
        { key: "desktop-live", onPress: () => router.push("/desktop-live" as Href) },
        { key: "theme", onPress: () => void toggleThemeMode() },
    ];

    if (status === "anonymous") return <Redirect href="/login" />;

    return (
        <SafeAreaView style={{ flex: 1, backgroundColor: colors.backgroundDeep }} edges={["top", "left", "right"]}>
            <PhoneTopbar actions={actions} userImageUri={userAvatarUri || undefined} onBrandPress={() => void goHomeToChat()} />
            <RpaPanelContent />
        </SafeAreaView>
    );
}
