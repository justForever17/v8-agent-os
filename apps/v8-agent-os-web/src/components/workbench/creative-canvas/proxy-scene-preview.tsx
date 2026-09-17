"use client";

import { Canvas, useThree } from "@react-three/fiber";
import { useEffect } from "react";
import { PerspectiveCamera } from "three";
import { interpolateVector, keyInterval, sampleEntity, type ProxyScene, type SceneEntity, type SceneVector } from "./proxy-scene";

const radians = (vector: SceneVector) => vector.map((value) => value * Math.PI / 180) as SceneVector;

function SceneCamera({ scene, time }: { scene: ProxyScene; time: number }) {
    const { camera, invalidate } = useThree();
    useEffect(() => {
        const interval = keyInterval(scene.camera.keyframes, time);
        if (!interval || !(camera instanceof PerspectiveCamera)) return;
        const [a, b, t] = interval;
        camera.position.set(...interpolateVector(a.position, b.position, t));
        camera.lookAt(...interpolateVector(a.target, b.target, t));
        // R3F owns a mutable Three camera; updating it is the renderer API.
        // eslint-disable-next-line react-hooks/immutability
        camera.fov = scene.camera.fov;
        camera.updateProjectionMatrix();
        invalidate();
    }, [camera, invalidate, scene, time]);
    return null;
}

function Entity({ entity, time, selected, onSelect }: { entity: SceneEntity; time: number; selected: boolean; onSelect: () => void }) {
    const sample = sampleEntity(entity, time);
    const material = <meshBasicMaterial color={entity.proxyColor} />;
    return <group position={sample.position} rotation={[...radians(sample.rotation), "ZYX"]} scale={entity.size} onClick={(event) => { event.stopPropagation(); onSelect(); }}>
        {entity.shape === "capsule" ? <>
            <mesh position={[0, .66, 0]} scale={[.72, .39, .68]}><sphereGeometry args={[.5, 12, 8]} />{material}</mesh>
            <mesh position={[0, .925, 0]} scale={[.49, .15, .60]}><sphereGeometry args={[.5, 12, 8]} />{material}</mesh>
            {([-1, 1] as const).map((side) => <group key={`arm-${side}`} position={[side * .43, .79, 0]} rotation={[sample.pose[side < 0 ? "leftArm" : "rightArm"] * Math.PI / 180, 0, 0]}>
                <mesh position={[0, -.16, 0]}><boxGeometry args={[.19, .32, .28]} />{material}</mesh>
            </group>)}
            {([-1, 1] as const).map((side) => <group key={`leg-${side}`} position={[side * .22, .49, 0]} rotation={[sample.pose[side < 0 ? "leftLeg" : "rightLeg"] * Math.PI / 180, 0, 0]}>
                <mesh position={[0, -.235, 0]}><boxGeometry args={[.27, .47, .43]} />{material}</mesh>
            </group>)}
        </> : <mesh position={[0, .5, 0]}>{entity.shape === "sphere" ? <sphereGeometry args={[.5, 12, 8]} /> : <boxGeometry />}{material}</mesh>}
        {selected ? <mesh position={[0, .5, 0]}><boxGeometry args={[1.04, 1.04, 1.04]} /><meshBasicMaterial color="#ffffff" wireframe transparent opacity={.4} /></mesh> : null}
    </group>;
}

export default function ProxyScenePreview({ scene, time, selectedId, onSelect }: { scene: ProxyScene; time: number; selectedId: string; onSelect: (id: string) => void }) {
    return <Canvas frameloop="demand" dpr={[1, 1.5]} camera={{ position: [0, 3, 8], near: .01, far: 1000 }} gl={{ antialias: true, preserveDrawingBuffer: true }}>
        <color attach="background" args={[scene.background]} />
        <gridHelper args={[20, 20, "#b7b4aa", "#d0cdc4"]} position={[0, -.02, 0]} />
        <SceneCamera scene={scene} time={time} />
        {scene.entities.map((entity) => <Entity key={entity.entityId} entity={entity} time={time} selected={selectedId === entity.entityId} onSelect={() => onSelect(entity.entityId)} />)}
    </Canvas>;
}
