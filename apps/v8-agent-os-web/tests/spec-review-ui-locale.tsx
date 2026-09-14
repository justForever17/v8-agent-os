/* eslint-disable @typescript-eslint/no-explicit-any, react-hooks/immutability -- Expose a synthetic locale control to the browser harness. */
import React, { useContext, useMemo, useState } from "react";
import zh from "../src/i18n/locales/zh-CN.json";
import en from "../src/i18n/locales/en.json";

const Context = React.createContext({ t: (key: string) => key });
export function FixtureLocale({ children }: { children: React.ReactNode }) {
    const [english, setEnglish] = useState(false);
    (window as any).fixtureLanguage = () => setEnglish(value => !value);
    const value = useMemo(() => ({ t: (key: string) => (english ? en : zh)[key as keyof typeof zh] || key }), [english]);
    return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useT() { return useContext(Context).t; }
