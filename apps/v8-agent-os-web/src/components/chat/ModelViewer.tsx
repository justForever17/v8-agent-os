"use client";

import { Canvas } from "@react-three/fiber";
import { Bounds, Center, OrbitControls, useAnimations, useGLTF } from "@react-three/drei";
import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { Box, FileWarning, Loader2 } from "lucide-react";
import { clone as cloneSkeleton } from "three/examples/jsm/utils/SkeletonUtils.js";

interface ModelViewerProps {
    src: string;
    className?: string;
    interactive?: boolean;
    active?: boolean;
    autoRotate?: boolean;
    compact?: boolean;
}

function Model({ url, active }: { url: string; active: boolean }) {
    const { scene, animations } = useGLTF(url);
    const copiedScene = useMemo(() => cloneSkeleton(scene), [scene]);
    const { actions, names } = useAnimations(animations, copiedScene);

    useEffect(() => {
        const action = names.length ? actions[names[0]] : undefined;
        if (!action) return;
        if (!active) {
            action.stop();
            return;
        }
        action.reset().fadeIn(0.15).play();
        return () => {
            action.fadeOut(0.1);
            action.stop();
        };
    }, [actions, active, names]);

    return <primitive object={copiedScene} />;
}

class ModelErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { failed: boolean }> {
    state = { failed: false };

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidUpdate(previous: Readonly<{ children: ReactNode; resetKey: string }>) {
        if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
    }

    render() {
        if (this.state.failed) {
            return (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
                    <FileWarning className="h-6 w-6" />
                    <span className="text-xs font-medium text-foreground">3D 文件暂时无法预览</span>
                    <span className="text-[10px]">可以继续下载原文件；画布不会修改它。</span>
                </div>
            );
        }
        return this.props.children;
    }
}

function useReducedMotionPreference() {
    const [reduced, setReduced] = useState(false);
    useEffect(() => {
        const query = window.matchMedia("(prefers-reduced-motion: reduce)");
        const update = () => setReduced(query.matches);
        update();
        query.addEventListener("change", update);
        return () => query.removeEventListener("change", update);
    }, []);
    return reduced;
}

function hasUnsupportedExplicitExtension(src: string) {
    const path = String(src || "").split(/[?#]/, 1)[0].toLowerCase();
    const extension = path.match(/\.([a-z0-9]+)$/)?.[1] || "";
    return Boolean(extension && !["glb", "gltf"].includes(extension));
}

export function ModelViewer({
    src,
    className = "",
    interactive = true,
    active = true,
    autoRotate = true,
    compact = false,
}: ModelViewerProps) {
    const reducedMotion = useReducedMotionPreference();
    const rotate = active && autoRotate && !reducedMotion;
    const controlsEnabled = active && interactive;
    const unsupported = hasUnsupportedExplicitExtension(src);

    return (
        <div className={`relative aspect-video w-full overflow-hidden rounded-lg border border-border bg-gray-50/50 dark:bg-gray-900/50 ${className}`}>
            {unsupported ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
                    <Box className="h-7 w-7" />
                    <span className="text-xs font-medium text-foreground">当前预览支持 GLB / GLTF</span>
                    <span className="text-[10px]">其他 3D 格式仍可作为来源、下载或交给主理人转换。</span>
                </div>
            ) : (
                <ModelErrorBoundary resetKey={src}>
                    <Suspense
                        fallback={(
                            <div className="absolute inset-0 flex items-center justify-center gap-2 text-muted-foreground">
                                <Loader2 className="h-5 w-5 animate-spin" />
                                <span className="text-xs font-medium">正在加载 3D 模型…</span>
                            </div>
                        )}
                    >
                        <Canvas
                            shadows={!compact}
                            dpr={compact ? [1, 1.25] : [1, 1.5]}
                            camera={{ fov: 45, near: 0.01, far: 10000, position: [0, 0, 5] }}
                            frameloop={active ? "always" : "demand"}
                        >
                            <ambientLight intensity={0.75} />
                            <hemisphereLight args={["#ffffff", "#64748b", 1.15]} />
                            <directionalLight position={[4, 7, 5]} intensity={1.7} castShadow={!compact} />
                            <directionalLight position={[-4, 2, -3]} intensity={0.55} />
                            <Bounds fit clip observe margin={1.2}>
                                <Center>
                                    <Model url={src} active={active} />
                                </Center>
                            </Bounds>
                            <OrbitControls
                                makeDefault
                                enabled={active}
                                enablePan={controlsEnabled}
                                enableRotate={controlsEnabled}
                                enableZoom={controlsEnabled}
                                autoRotate={rotate}
                                autoRotateSpeed={0.5}
                            />
                        </Canvas>
                    </Suspense>
                </ModelErrorBoundary>
            )}

            {!compact ? (
                <div className="pointer-events-none absolute right-2 top-2 flex items-center gap-1.5 rounded border bg-background/80 px-2 py-1 text-[10px] font-mono opacity-65 shadow-sm backdrop-blur">
                    <Box className="h-3 w-3" />
                    3D PREVIEW
                </div>
            ) : null}
        </div>
    );
}
