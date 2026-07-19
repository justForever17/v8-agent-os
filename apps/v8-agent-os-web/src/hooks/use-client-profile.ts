"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSession } from "next-auth/react";

import { getUserProfile, type SharedUserProfile } from "@/lib/actions/user.actions";
import { normalizeAppearance } from "@/lib/personalization";

const PROFILE_UPDATED_EVENT = "v8-client-profile-updated";
let sharedProfileRequest: Promise<SharedUserProfile | null> | null = null;
let sharedProfileSnapshot: SharedUserProfile | null | undefined;
let sharedProfileFetchedAt = 0;

async function loadSharedProfile() {
    if (sharedProfileRequest) return sharedProfileRequest;
    if (sharedProfileSnapshot !== undefined && Date.now() - sharedProfileFetchedAt < 3_000) {
        return sharedProfileSnapshot;
    }
    const request = (async () => {
        const result = await getUserProfile();
        const next = result.success && result.user ? result.user : null;
        sharedProfileSnapshot = next;
        sharedProfileFetchedAt = Date.now();
        return next;
    })();
    sharedProfileRequest = request;
    try {
        return await request;
    } finally {
        if (sharedProfileRequest === request) sharedProfileRequest = null;
    }
}

function normalizeProfileValue(value?: string | null) {
    return String(value || "").trim();
}

function profilesMatch(left?: SharedUserProfile | null, right?: SharedUserProfile | null) {
    return normalizeProfileValue(left?.login) === normalizeProfileValue(right?.login)
        && normalizeProfileValue(left?.email) === normalizeProfileValue(right?.email)
        && normalizeProfileValue(left?.name) === normalizeProfileValue(right?.name)
        && normalizeProfileValue(left?.image) === normalizeProfileValue(right?.image)
        && JSON.stringify(normalizeAppearance(left?.appearance)) === JSON.stringify(normalizeAppearance(right?.appearance))
        && normalizeProfileValue(left?.role) === normalizeProfileValue(right?.role);
}

function sessionFieldsMatch(left?: SharedUserProfile | null, right?: SharedUserProfile | null) {
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
    sharedProfileSnapshot = profile;
    sharedProfileFetchedAt = Date.now();
    window.dispatchEvent(new CustomEvent<SharedUserProfile | null>(PROFILE_UPDATED_EVENT, { detail: profile }));
}

export function useClientProfile() {
    const { data: session, status, update } = useSession();
    const [profile, setProfile] = useState<SharedUserProfile | null>(null);
    const [loading, setLoading] = useState(false);
    const [canonicalLoaded, setCanonicalLoaded] = useState(false);
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
        setCanonicalLoaded(true);
        setProfile((current) => profilesMatch(current, next) ? current : next);
        emitProfileUpdate(next);
        if (!next) return;
        // Appearance is canonical profile data, not part of the NextAuth session.
        // Comparing it here would make every profile consumer refresh the session forever.
        if (sessionFieldsMatch(next, sessionProfile)) return;
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

    const refreshProfile = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
        if (!hasSessionUser) {
            setProfile(null);
            setCanonicalLoaded(true);
            return null;
        }
        if (!silent) setLoading(true);
        try {
            const next = await loadSharedProfile();
            await applyProfile(next);
            return next;
        } finally {
            if (!silent) setLoading(false);
        }
    }, [applyProfile, hasSessionUser]);

    useEffect(() => {
        if (status !== "authenticated") {
            setProfile(null);
            setCanonicalLoaded(status === "unauthenticated");
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

    useEffect(() => {
        if (status !== "authenticated" || typeof window === "undefined") return;
        const refreshSilently = () => { void refreshProfile({ silent: true }); };
        const handleVisibility = () => {
            if (document.visibilityState === "visible") refreshSilently();
        };
        window.addEventListener("focus", refreshSilently);
        document.addEventListener("visibilitychange", handleVisibility);
        const timer = window.setInterval(() => {
            if (document.visibilityState === "visible") refreshSilently();
        }, 10_000);
        return () => {
            window.removeEventListener("focus", refreshSilently);
            document.removeEventListener("visibilitychange", handleVisibility);
            window.clearInterval(timer);
        };
    }, [refreshProfile, status]);

    return {
        status,
        profile: profile || sessionProfile,
        loading,
        canonicalLoaded,
        refreshProfile,
        applyProfile,
    };
}
