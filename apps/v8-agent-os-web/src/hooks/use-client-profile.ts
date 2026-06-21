"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSession } from "next-auth/react";

import { getUserProfile, type SharedUserProfile } from "@/lib/actions/user.actions";

export function resolveProfileAvatarSrc(image?: string | null) {
    const raw = String(image || "").trim();
    if (!raw) return "";
    if (raw.startsWith("/")) {
        return `/api/avatar?src=${encodeURIComponent(raw)}`;
    }
    return raw;
}

export function useClientProfile() {
    const { data: session, status, update } = useSession();
    const [profile, setProfile] = useState<SharedUserProfile | null>(null);
    const [loading, setLoading] = useState(false);

    const sessionProfile = useMemo<SharedUserProfile | null>(() => {
        if (!session?.user) return null;
        return {
            id: session.user.id,
            login: session.user.login,
            email: session.user.email || "",
            name: session.user.name || "",
            image: session.user.image || "",
            role: session.user.role,
        };
    }, [session?.user]);

    const applyProfile = useCallback(async (next: SharedUserProfile | null) => {
        setProfile(next);
        if (!next) return;
        await update({
            login: next.login,
            role: next.role,
            email: next.email,
            name: next.name,
            image: next.image,
        });
    }, [update]);

    const refreshProfile = useCallback(async () => {
        if (!session?.user) {
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
    }, [applyProfile, session?.user]);

    useEffect(() => {
        if (status !== "authenticated") {
            setProfile(null);
            return;
        }
        void refreshProfile();
    }, [refreshProfile, status]);

    return {
        status,
        profile: profile || sessionProfile,
        loading,
        refreshProfile,
        applyProfile,
    };
}
