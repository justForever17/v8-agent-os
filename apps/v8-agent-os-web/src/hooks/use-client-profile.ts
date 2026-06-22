"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSession } from "next-auth/react";

import { getUserProfile, type SharedUserProfile } from "@/lib/actions/user.actions";

const PROFILE_UPDATED_EVENT = "v8-client-profile-updated";

function normalizeProfileValue(value?: string | null) {
    return String(value || "").trim();
}

function profilesMatch(left?: SharedUserProfile | null, right?: SharedUserProfile | null) {
    return normalizeProfileValue(left?.login) === normalizeProfileValue(right?.login)
        && normalizeProfileValue(left?.email) === normalizeProfileValue(right?.email)
        && normalizeProfileValue(left?.name) === normalizeProfileValue(right?.name)
        && normalizeProfileValue(left?.image) === normalizeProfileValue(right?.image)
        && normalizeProfileValue(left?.role) === normalizeProfileValue(right?.role);
}

export function resolveProfileAvatarSrc(image?: string | null) {
    const raw = String(image || "").trim();
    if (!raw) return "";
    if (raw.startsWith("/")) {
        return `/api/avatar?src=${encodeURIComponent(raw)}`;
    }
    return raw;
}

function emitProfileUpdate(profile: SharedUserProfile | null) {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent<SharedUserProfile | null>(PROFILE_UPDATED_EVENT, { detail: profile }));
}

export function useClientProfile() {
    const { data: session, status, update } = useSession();
    const [profile, setProfile] = useState<SharedUserProfile | null>(null);
    const [loading, setLoading] = useState(false);
    const sessionUserId = session?.user?.id;
    const sessionUserLogin = session?.user?.login;
    const sessionUserEmail = session?.user?.email;
    const sessionUserName = session?.user?.name;
    const sessionUserImage = session?.user?.image;
    const sessionUserRole = session?.user?.role;
    const hasSessionUser = Boolean(sessionUserId || sessionUserLogin || sessionUserEmail);

    const sessionProfile = useMemo<SharedUserProfile | null>(() => {
        if (!hasSessionUser) return null;
        return {
            id: sessionUserId,
            login: sessionUserLogin,
            email: sessionUserEmail || "",
            name: sessionUserName || "",
            image: sessionUserImage || "",
            role: sessionUserRole,
        };
    }, [hasSessionUser, sessionUserEmail, sessionUserId, sessionUserImage, sessionUserLogin, sessionUserName, sessionUserRole]);

    const applyProfile = useCallback(async (next: SharedUserProfile | null) => {
        setProfile((current) => profilesMatch(current, next) ? current : next);
        emitProfileUpdate(next);
        if (!next) return;
        if (profilesMatch(next, sessionProfile)) return;
        void update({
            login: next.login,
            role: next.role,
            email: next.email,
            name: next.name,
            image: next.image,
        }).catch((error) => {
            console.warn("[useClientProfile] Failed to sync NextAuth session profile:", error);
        });
    }, [sessionProfile, update]);

    const refreshProfile = useCallback(async () => {
        if (!hasSessionUser) {
            setProfile(null);
            return null;
        }
        setLoading(true);
        try {
            const result = await getUserProfile();
            if (result.success && result.user) {
                await applyProfile(result.user);
                return result.user;
            }
            return null;
        } finally {
            setLoading(false);
        }
    }, [applyProfile, hasSessionUser]);

    useEffect(() => {
        if (status !== "authenticated") {
            setProfile(null);
            return;
        }
        void refreshProfile();
    }, [refreshProfile, status]);

    useEffect(() => {
        if (typeof window === "undefined") {
            return;
        }
        const handleProfileUpdate = (event: Event) => {
            const next = (event as CustomEvent<SharedUserProfile | null>).detail || null;
            setProfile((current) => profilesMatch(current, next) ? current : next);
        };
        window.addEventListener(PROFILE_UPDATED_EVENT, handleProfileUpdate);
        return () => window.removeEventListener(PROFILE_UPDATED_EVENT, handleProfileUpdate);
    }, []);

    return {
        status,
        profile: profile || sessionProfile,
        loading,
        refreshProfile,
        applyProfile,
    };
}
