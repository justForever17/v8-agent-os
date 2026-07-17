import { useCallback, useEffect, useRef, useState } from "react";
import {
    cancelAnimation,
    Easing,
    runOnJS,
    useReducedMotion,
    useSharedValue,
    withTiming,
} from "react-native-reanimated";

type DeferredModalMotionOptions = {
    enterDuration: number;
    exitDuration: number;
};

export function useDeferredModalMotion(
    visible: boolean,
    { enterDuration, exitDuration }: DeferredModalMotionOptions,
) {
    const reduceMotion = useReducedMotion();
    const [rendered, setRendered] = useState(visible);
    const renderedRef = useRef(visible);
    const progress = useSharedValue(visible ? 1 : 0);

    const finishClose = useCallback(() => {
        renderedRef.current = false;
        setRendered(false);
    }, []);

    useEffect(() => {
        let frame: number | null = null;
        cancelAnimation(progress);

        if (visible) {
            renderedRef.current = true;
            setRendered(true);
            progress.value = 0;
            frame = requestAnimationFrame(() => {
                progress.value = withTiming(1, {
                    duration: reduceMotion ? 120 : enterDuration,
                    easing: Easing.bezier(0.32, 0.72, 0, 1),
                });
            });
        } else if (renderedRef.current) {
            progress.value = withTiming(0, {
                duration: reduceMotion ? 100 : exitDuration,
                easing: Easing.bezier(0.32, 0.72, 0, 1),
            }, (finished) => {
                if (finished) runOnJS(finishClose)();
            });
        }

        return () => {
            if (frame !== null) cancelAnimationFrame(frame);
        };
    }, [enterDuration, exitDuration, finishClose, progress, reduceMotion, visible]);

    useEffect(() => () => cancelAnimation(progress), [progress]);

    return { progress, reduceMotion, rendered };
}
