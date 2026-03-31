"use client";

import { Canvas } from "@react-three/fiber";
import { useGLTF, Stage, OrbitControls } from "@react-three/drei";
import { Suspense, useMemo } from "react";
import { Loader2, Box } from "lucide-react";

interface ModelViewerProps {
    src: string;
    className?: string;
}

function Model({ url }: { url: string }) {
    const { scene } = useGLTF(url);
    const copiedScene = useMemo(() => scene.clone(), [scene]);
    return <primitive object={copiedScene} />;
}

export function ModelViewer({ src, className }: ModelViewerProps) {
    return (
        <div className={`relative w-full aspect-video bg-gray-50/50 dark:bg-gray-900/50 rounded-lg border border-border overflow-hidden ${className}`}>
            <Suspense
                fallback={
                    <div className="absolute inset-0 flex items-center justify-center text-muted-foreground gap-2">
                        <Loader2 className="w-5 h-5 animate-spin" />
                        <span className="text-sm font-medium">Loading 3D Model...</span>
                    </div>
                }
            >
                <Canvas shadows dpr={[1, 2]} camera={{ fov: 50 }}>
                    <Stage environment="city" intensity={0.6}>
                        <Model url={src} />
                    </Stage>
                    <OrbitControls makeDefault autoRotate autoRotateSpeed={0.5} />
                </Canvas>
            </Suspense>

            <div className="absolute top-2 right-2 px-2 py-1 bg-background/80 backdrop-blur rounded text-[10px] font-mono border shadow-sm flex items-center gap-1.5 opacity-60 hover:opacity-100 transition-opacity">
                <Box className="w-3 h-3" />
                3D PREVIEW
            </div>
        </div>
    );
}
