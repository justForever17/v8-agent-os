"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { Loader2, Monitor, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

type DesktopLiveStatus = {
    available?: boolean;
    reason?: string | null;
    activeSessionId?: string | null;
    viewerCount?: number;
    config?: {
        enabled?: boolean;
        maxWidth?: number;
        maxHeight?: number;
        targetFps?: number;
    };
};

type DesktopLiveAnswer = {
    sessionId?: string;
    type?: RTCSdpType;
    sdp?: string;
    error?: string;
};

export function DesktopLiveButton() {
    const pathname = usePathname();
    const { status: authStatus, data: session } = useSession();
    const [mounted, setMounted] = useState(false);
    const [liveStatus, setLiveStatus] = useState<DesktopLiveStatus | null>(null);
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [videoReady, setVideoReady] = useState(false);
    const t = useT();
    const videoRef = useRef<HTMLVideoElement | null>(null);
    const sessionIdRef = useRef<string | null>(null);
    const connectionRef = useRef<RTCPeerConnection | null>(null);
    const remoteStreamRef = useRef<MediaStream | null>(null);
    const closingRef = useRef(false);
    const lastPathnameRef = useRef(pathname);

    useEffect(() => {
        setMounted(true);
    }, []);

    const visible = useMemo(() => {
        if (!pathname.startsWith("/chat")) return false;
        if (authStatus !== "authenticated") return false;
        return String(session?.user?.role || "").toUpperCase() !== "ADMIN";
    }, [authStatus, pathname, session?.user?.role]);

    const refreshStatus = useCallback(async () => {
        if (!visible) return;
        try {
            const response = await fetch("/api/desktop-live/status", { cache: "no-store" });
            const payload = (await response.json().catch(() => ({}))) as DesktopLiveStatus & { error?: string };
            if (!response.ok) {
                setLiveStatus({ available: false, reason: payload.error || t(lt("桌面直播当前不可用", "Desktop Live is unavailable")) });
                return;
            }
            setLiveStatus(payload);
        } catch (err) {
            setLiveStatus({
                available: false,
                reason: err instanceof Error ? err.message : t(lt("桌面直播当前不可用", "Desktop Live is unavailable")),
            });
        }
    }, [t, visible]);

    const releaseSession = useCallback(async (activeSessionId: string | null) => {
        if (!activeSessionId) return;
        await fetch("/api/desktop-live/release", {
            method: "POST",
            credentials: "include",
            headers: {
                "content-type": "application/json",
            },
            body: JSON.stringify({ sessionId: activeSessionId }),
            keepalive: true,
        }).catch(() => undefined);
    }, []);

    const closeViewer = useCallback(async () => {
        if (closingRef.current) return;
        closingRef.current = true;
        const activeSessionId = sessionIdRef.current;
        setLoading(false);
        setOpen(false);
        setError(null);
        setVideoReady(false);
        setSessionId(null);
        sessionIdRef.current = null;

        const pc = connectionRef.current;
        connectionRef.current = null;
        if (pc) {
            try {
                pc.close();
            } catch {
                // noop
            }
        }

        if (videoRef.current) {
            videoRef.current.pause();
            videoRef.current.srcObject = null;
        }
        if (remoteStreamRef.current) {
            remoteStreamRef.current.getTracks().forEach((track) => track.stop());
            remoteStreamRef.current = null;
        }

        await releaseSession(activeSessionId);
        await refreshStatus();
        closingRef.current = false;
    }, [refreshStatus, releaseSession]);

    useEffect(() => {
        if (!visible) {
            setLiveStatus(null);
            return;
        }
        void refreshStatus();
    }, [refreshStatus, visible]);

    useEffect(() => {
        if (!open) return;
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                void closeViewer();
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [closeViewer, open]);

    useEffect(() => {
        sessionIdRef.current = sessionId;
    }, [sessionId]);

    useEffect(() => {
        return () => {
            const activeSessionId = sessionIdRef.current;
            const pc = connectionRef.current;
            if (pc) {
                try {
                    pc.close();
                } catch {
                    // noop
                }
            }
            if (activeSessionId) {
                void releaseSession(activeSessionId);
            }
        };
    }, [releaseSession]);

    useEffect(() => {
        if (!open) {
            lastPathnameRef.current = pathname;
            return;
        }
        if (lastPathnameRef.current !== pathname) {
            void closeViewer();
        }
        lastPathnameRef.current = pathname;
    }, [closeViewer, open, pathname]);

    const openViewer = useCallback(async () => {
        if (loading) return;
        setOpen(true);
        setLoading(true);
        setError(null);
        setVideoReady(false);
        let activeSessionId: string | null = null;

        try {
            const sessionResponse = await fetch("/api/desktop-live/session", {
                method: "POST",
                cache: "no-store",
            });
            const sessionPayload = (await sessionResponse.json().catch(() => ({}))) as { sessionId?: string; error?: string };
            if (!sessionResponse.ok || !sessionPayload.sessionId) {
                throw new Error(sessionPayload.error || t(lt("创建桌面直播会话失败", "Failed to create Desktop Live session")));
            }

            activeSessionId = sessionPayload.sessionId;
            sessionIdRef.current = activeSessionId;
            setSessionId(activeSessionId);

            const pc = new RTCPeerConnection({ iceServers: [] });
            connectionRef.current = pc;
            const remoteStream = new MediaStream();
            remoteStreamRef.current = remoteStream;

            pc.addTransceiver("video", { direction: "recvonly" });

            pc.ontrack = (event) => {
                const target = videoRef.current;
                if (!target) return;
                const stream = event.streams[0] || remoteStream;
                if (event.streams.length === 0) {
                    remoteStream.addTrack(event.track);
                }
                target.srcObject = stream;
                void target.play().catch(() => undefined);
                setVideoReady(true);
                setLoading(false);
            };

            pc.onconnectionstatechange = () => {
                if (pc.connectionState === "failed") {
                    setError(t(lt("桌面直播连接失败，请重试。", "Desktop Live failed to connect. Please retry.")));
                    setLoading(false);
                }
                if ((pc.connectionState === "disconnected" || pc.connectionState === "closed") && !closingRef.current) {
                    setError(t(lt("桌面直播已断开。", "Desktop Live disconnected.")));
                }
            };

            pc.onicecandidate = (event) => {
                if (!activeSessionId) return;
                void fetch("/api/desktop-live/candidate", {
                    method: "POST",
                    headers: {
                        "content-type": "application/json",
                    },
                    body: JSON.stringify({
                        sessionId: activeSessionId,
                        candidate: event.candidate ? event.candidate.toJSON() : null,
                    }),
                }).catch(() => undefined);
            };

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            const localDescription = pc.localDescription ?? offer;
            if (!localDescription.sdp) {
                throw new Error(t(lt("桌面直播本地描述生成失败。", "Failed to create a Desktop Live local description.")));
            }

            const offerResponse = await fetch("/api/desktop-live/offer", {
                method: "POST",
                headers: {
                    "content-type": "application/json",
                },
                body: JSON.stringify({
                    sessionId: activeSessionId,
                    sdp: localDescription.sdp,
                    type: localDescription.type,
                }),
                cache: "no-store",
            });
            const answerPayload = (await offerResponse.json().catch(() => ({}))) as DesktopLiveAnswer;
            if (!offerResponse.ok || !answerPayload.sdp || !answerPayload.type) {
                throw new Error(answerPayload.error || t(lt("桌面直播协商失败", "Desktop Live negotiation failed")));
            }

            await pc.setRemoteDescription({
                type: answerPayload.type,
                sdp: answerPayload.sdp,
            });

            setLoading(false);
            await refreshStatus();
        } catch (err) {
            const pc = connectionRef.current;
            connectionRef.current = null;
            if (pc) {
                try {
                    pc.close();
                } catch {
                    // noop
                }
            }
            if (activeSessionId) {
                void releaseSession(activeSessionId);
            }
            sessionIdRef.current = null;
            setSessionId(null);
            setLoading(false);
            setError(err instanceof Error ? err.message : t(lt("创建桌面直播会话失败", "Failed to create Desktop Live session")));
        }
    }, [loading, refreshStatus, releaseSession, t]);

    if (!visible) {
        return null;
    }

    const disabled = !liveStatus?.available;
    const buttonTitle = disabled ? liveStatus?.reason || t(lt("桌面直播当前不可用", "Desktop Live is unavailable")) : t(lt("观看服务端真实桌面", "Watch the live desktop"));

    return (
        <>
            <Button
                type="button"
                variant="ghost"
                size="icon"
                className="relative h-9 w-9 rounded-xl"
                title={buttonTitle}
                disabled={disabled || loading}
                onClick={() => void openViewer()}
                onPointerEnter={() => void refreshStatus()}
            >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Monitor className="h-4 w-4" />}
                {liveStatus?.activeSessionId ? (
                    <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.8)]" />
                ) : null}
            </Button>

            {mounted && open
                ? createPortal(
                    <div
                        className="fixed inset-0 z-[120] bg-black/72 backdrop-blur-sm"
                        onClick={() => void closeViewer()}
                    >
                        <button
                            type="button"
                            aria-label={t(lt("关闭直播", "Close stream"))}
                            className="absolute right-[max(12px,calc(env(safe-area-inset-right)+12px))] top-[max(12px,calc(env(safe-area-inset-top)+12px))] z-[130] flex h-11 w-11 items-center justify-center rounded-full bg-black/72 text-white shadow-lg transition hover:bg-black/85"
                            onClick={(event) => {
                                event.stopPropagation();
                                void closeViewer();
                            }}
                        >
                            <X className="h-5 w-5" />
                        </button>

                        <div className="grid h-full w-full place-items-center p-[max(12px,calc(env(safe-area-inset-top)+12px))]">
                            <div
                                className="relative flex w-full items-center justify-center"
                                onClick={(event) => event.stopPropagation()}
                            >
                                <video
                                    ref={videoRef}
                                    autoPlay
                                    playsInline
                                    muted
                                    className="block h-auto max-h-[calc(100svh-env(safe-area-inset-top)-env(safe-area-inset-bottom)-24px)] w-full max-w-[min(calc(100vw-24px),1180px)] select-none rounded-[24px] bg-black object-contain shadow-[0_30px_120px_rgba(0,0,0,0.45)]"
                                />
                                {(loading || (!videoReady && !error)) ? (
                                    <div className="absolute inset-0 flex items-center justify-center rounded-[24px] bg-black/55">
                                        <div className="rounded-full bg-black/70 px-5 py-3 text-sm text-zinc-100">
                                            {t(lt("正在建立桌面流连接…", "Connecting to Desktop Live..."))}
                                        </div>
                                    </div>
                                ) : null}
                                {error ? (
                                    <div className="absolute inset-0 flex items-center justify-center rounded-[24px] bg-black/55 px-6 text-center">
                                        <div className="rounded-full bg-black/75 px-5 py-3 text-sm text-zinc-100">
                                            {error}
                                        </div>
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    </div>,
                    document.body,
                )
                : null}
        </>
    );
}
