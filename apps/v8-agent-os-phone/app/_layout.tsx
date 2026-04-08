import { Fragment, useEffect } from "react";
import { Platform } from "react-native";
import { DarkTheme, DefaultTheme, ThemeProvider } from "@react-navigation/native";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as SplashScreen from "expo-splash-screen";
import "react-native-reanimated";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { AppSessionProvider } from "@/src/providers/app-session";
import { UiPrefsProvider, useUiPrefs } from "@/src/providers/ui-prefs";

export {
    ErrorBoundary,
} from "expo-router";

export const unstable_settings = {
    initialRouteName: "index",
};

void SplashScreen.preventAutoHideAsync().catch(() => undefined);

function AppNavigation() {
    const { colors, themeMode } = useUiPrefs();
    const base = themeMode === "dark" ? DarkTheme : DefaultTheme;
    const theme = {
        ...base,
        colors: {
            ...base.colors,
            background: colors.background,
            card: colors.surface,
            border: colors.border,
            primary: colors.primary,
            text: colors.text,
        },
    };

    useEffect(() => {
        void SplashScreen.hideAsync().catch(() => undefined);
    }, []);

    return (
        <ThemeProvider value={theme}>
            <AppSessionProvider>
                <StatusBar style={themeMode === "dark" ? "light" : "dark"} />
                <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }}>
                    <Stack.Screen name="index" />
                    <Stack.Screen name="login" />
                    <Stack.Screen name="chat" />
                    <Stack.Screen name="connect" />
                    <Stack.Screen name="artifacts" />
                    <Stack.Screen name="desktop-live" />
                    <Stack.Screen name="rpa" />
                    <Stack.Screen name="sessions" />
                    <Stack.Screen name="approvals" />
                    <Stack.Screen name="settings" />
                </Stack>
            </AppSessionProvider>
        </ThemeProvider>
    );
}

export default function RootLayout() {
    return (
        <UiPrefsProvider>
            {Platform.OS === "web" ? (
                <Fragment>
                    <AppNavigation />
                </Fragment>
            ) : (
                <KeyboardProvider statusBarTranslucent navigationBarTranslucent>
                    <AppNavigation />
                </KeyboardProvider>
            )}
        </UiPrefsProvider>
    );
}
