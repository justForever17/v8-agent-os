import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from "react";

type HistoryState<T> = { past: T[]; future: T[] };

export function useCanvasHistory<T>(initialValue: T, limit = 60) {
    const [value, replaceValue] = useState(initialValue);
    const [, setHistoryVersion] = useState(0);
    const valueRef = useRef(value);
    const historyRef = useRef<HistoryState<T>>({ past: [], future: [] });
    const transactionRef = useRef<T | null>(null);
    valueRef.current = value;

    const publish = useCallback((next: T) => {
        valueRef.current = next;
        replaceValue(next);
    }, []);

    const reset = useCallback((next: T) => {
        historyRef.current = { past: [], future: [] };
        transactionRef.current = null;
        publish(next);
    }, [publish]);

    const replace: Dispatch<SetStateAction<T>> = useCallback((updater) => {
        const next = typeof updater === "function"
            ? (updater as (current: T) => T)(valueRef.current)
            : updater;
        publish(next);
    }, [publish]);

    const commit: Dispatch<SetStateAction<T>> = useCallback((updater) => {
        const previous = valueRef.current;
        const next = typeof updater === "function"
            ? (updater as (current: T) => T)(previous)
            : updater;
        if (Object.is(previous, next)) return;
        historyRef.current = {
            past: [...historyRef.current.past, previous].slice(-limit),
            future: [],
        };
        setHistoryVersion((current) => current + 1);
        publish(next);
    }, [limit, publish]);

    const beginTransaction = useCallback(() => {
        if (transactionRef.current === null) transactionRef.current = valueRef.current;
    }, []);

    const finishTransaction = useCallback(() => {
        const previous = transactionRef.current;
        transactionRef.current = null;
        if (previous === null || Object.is(previous, valueRef.current)) return;
        historyRef.current = {
            past: [...historyRef.current.past, previous].slice(-limit),
            future: [],
        };
        setHistoryVersion((current) => current + 1);
    }, [limit]);

    const cancelTransaction = useCallback(() => {
        const previous = transactionRef.current;
        transactionRef.current = null;
        if (previous !== null) publish(previous);
    }, [publish]);

    const undo = useCallback(() => {
        const previous = historyRef.current.past.at(-1);
        if (previous === undefined) return false;
        historyRef.current = {
            past: historyRef.current.past.slice(0, -1),
            future: [valueRef.current, ...historyRef.current.future].slice(0, limit),
        };
        publish(previous);
        return true;
    }, [limit, publish]);

    const redo = useCallback(() => {
        const next = historyRef.current.future[0];
        if (next === undefined) return false;
        historyRef.current = {
            past: [...historyRef.current.past, valueRef.current].slice(-limit),
            future: historyRef.current.future.slice(1),
        };
        publish(next);
        return true;
    }, [limit, publish]);

    return {
        value,
        replace,
        commit,
        reset,
        beginTransaction,
        finishTransaction,
        cancelTransaction,
        undo,
        redo,
        canUndo: historyRef.current.past.length > 0,
        canRedo: historyRef.current.future.length > 0,
    };
}
