export type ThemeMode = "light" | "dark";

export type ThemeColors = {
    background: string;
    backgroundDeep: string;
    surface: string;
    surfaceMuted: string;
    surfaceStrong: string;
    text: string;
    textMuted: string;
    textSoft: string;
    border: string;
    primary: string;
    primaryDeep: string;
    primarySoft: string;
    accent: string;
    accentSoft: string;
    success: string;
    warning: string;
    danger: string;
    chip: string;
    overlay: string;
    assistantBubble: string;
    userBubbleTop: string;
    userBubbleBottom: string;
};

export const lightColors: ThemeColors = {
    background: "#EEF2FF",
    backgroundDeep: "#F8FAFC",
    surface: "#FFFFFF",
    surfaceMuted: "rgba(255,255,255,0.72)",
    surfaceStrong: "rgba(255,255,255,0.9)",
    text: "#0F172A",
    textMuted: "#64748B",
    textSoft: "#94A3B8",
    border: "rgba(148, 163, 184, 0.22)",
    primary: "#7C3AED",
    primaryDeep: "#5B21B6",
    primarySoft: "rgba(124, 58, 237, 0.12)",
    accent: "#F97316",
    accentSoft: "rgba(249, 115, 22, 0.14)",
    success: "#10B981",
    warning: "#F59E0B",
    danger: "#EF4444",
    chip: "rgba(255,255,255,0.82)",
    overlay: "rgba(15, 23, 42, 0.38)",
    assistantBubble: "#FFFFFF",
    userBubbleTop: "#8B5CF6",
    userBubbleBottom: "#6D28D9",
};

export const darkColors: ThemeColors = {
    background: "#070B16",
    backgroundDeep: "#0F172A",
    surface: "rgba(15,23,42,0.96)",
    surfaceMuted: "rgba(15,23,42,0.82)",
    surfaceStrong: "rgba(15,23,42,0.92)",
    text: "#F8FAFC",
    textMuted: "#CBD5E1",
    textSoft: "#94A3B8",
    border: "rgba(148,163,184,0.20)",
    primary: "#8B5CF6",
    primaryDeep: "#C4B5FD",
    primarySoft: "rgba(139,92,246,0.18)",
    accent: "#FB923C",
    accentSoft: "rgba(251,146,60,0.18)",
    success: "#34D399",
    warning: "#FBBF24",
    danger: "#FB7185",
    chip: "rgba(15,23,42,0.82)",
    overlay: "rgba(2,6,23,0.62)",
    assistantBubble: "rgba(15,23,42,0.96)",
    userBubbleTop: "#7C3AED",
    userBubbleBottom: "#6D28D9",
};

export function getThemeColors(mode: ThemeMode): ThemeColors {
    return mode === "dark" ? darkColors : lightColors;
}

export const colors = lightColors;

export const spacing = {
    xs: 6,
    sm: 10,
    md: 14,
    lg: 18,
    xl: 24,
    xxl: 32,
} as const;

export const radii = {
    sm: 12,
    md: 18,
    lg: 24,
    xl: 30,
    pill: 999,
} as const;
