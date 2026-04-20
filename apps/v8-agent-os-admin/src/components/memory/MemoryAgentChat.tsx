"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Send, Loader2, Zap } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";

interface MessagePart {
    type: 'text' | 'tool_call' | 'tool_result' | 'thinking';
    content?: string;
    toolName?: string;
    toolArgs?: Record<string, unknown>;
    result?: string;
    isFinished?: boolean;
}

interface Message {
    role: "user" | "assistant";
    parts: MessagePart[];
}

const QUICK_CMDS = [
    { label: "components.memory.MemoryAgentChat.k5a681356", prompt: "components.memory.MemoryAgentChat.k5f44801f" },
    { label: "components.memory.MemoryAgentChat.k2de5b426", prompt: "components.memory.MemoryAgentChat.k407ff049" },
    { label: "components.memory.MemoryAgentChat.k6dd95b5d", prompt: "components.memory.MemoryAgentChat.k376dd616" },
    { label: "components.memory.MemoryAgentChat.k78d55023", prompt: "components.memory.MemoryAgentChat.kb2b16645" },
];

export default function MemoryAgentChat() {
    const t = useT();
    const [messages, setMessages] = useState<Message[]>([
        { role: "assistant", parts: [{ type: 'text', content: t("components.memory.MemoryAgentChat.k764f5071") }] }
    ]);
    const [input, setInput] = useState("");
    const [loading, setLoading] = useState(false);
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    const sendMessage = async (text: string) => {
        const userMsg = text.trim();
        if (!userMsg || loading) return;

        setMessages(prev => [...prev, { role: "user", parts: [{ type: 'text', content: userMsg }] }]);
        setInput("");
        setLoading(true);

        try {
            const res = await fetch("/api/memory/admin-chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: userMsg }),
            });

            if (!res.ok || !res.body) throw new Error("Stream failed");

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            // Parse Server-Sent Events
            setMessages(prev => [...prev, { role: "assistant", parts: [] }]);

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value);
                // 解析 NDJSON 行
                const lines = chunk.split("\n");
                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const parsed = JSON.parse(line);
                        
                        setMessages(prev => {
                            const updated = [...prev];
                            const lastMsg = { ...updated[updated.length - 1] };
                            const parts = [...(lastMsg.parts || [])];

                            if (parsed.type === "text" || parsed.token) {
                                // merge into last text part if possible
                                const lastPart = parts[parts.length - 1];
                                if (lastPart && lastPart.type === 'text') {
                                    parts[parts.length - 1] = {
                                        ...lastPart,
                                        content: (lastPart.content || "") + (parsed.token || "")
                                    };
                                } else {
                                    parts.push({ type: 'text', content: parsed.token || "" });
                                }
                            } else if (parsed.type === "thinking") {
                                const lastPart = parts[parts.length - 1];
                                if (lastPart && lastPart.type === 'thinking') {
                                    parts[parts.length - 1] = {
                                        ...lastPart,
                                        content: (lastPart.content || "") + (parsed.token || "")
                                    };
                                } else {
                                    parts.push({ type: 'thinking', content: parsed.token || "" });
                                }
                            } else if (parsed.type === "tool_call") {
                                parts.push({ 
                                    type: 'tool_call', 
                                    toolName: parsed.name, 
                                    toolArgs: parsed.arguments,
                                    isFinished: false 
                                });
                            } else if (parsed.type === "tool_result") {
                                // mark previous tool call as finished
                                for (let i = parts.length - 1; i >= 0; i--) {
                                    if (parts[i].type === 'tool_call' && !parts[i].isFinished) {
                                        parts[i] = {
                                            ...parts[i],
                                            result: parsed.result,
                                            isFinished: true
                                        };
                                        break;
                                    }
                                }
                            } else if (parsed.type === "error") {
                                parts.push({ type: 'text', content: `\n\n**Error:** ${parsed.error}` });
                            }

                            lastMsg.parts = parts;
                            updated[updated.length - 1] = lastMsg;
                            return updated;
                        });

                    } catch { /* ignore parse errors for partial lines */ }
                }
            }
        } catch (err) {
            console.error("Chat error:", err);
            setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1].parts.push({ type: 'text', content: t("components.memory.MemoryAgentChat.kf71ee052") });
                return updated;
            });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex flex-col h-[600px] rounded-lg border bg-background overflow-hidden">
            {/* 快捷指令 */}
            <div className="flex gap-2 flex-wrap p-3 border-b bg-muted/20">
                <Zap className="w-4 h-4 text-primary mt-0.5 shrink-0" />
                        {QUICK_CMDS.map((cmd) => (
                    <Button
                        key={cmd.label}
                        variant="outline"
                        size="sm"
                        className="text-xs h-7"
                        onClick={() => sendMessage(t(cmd.prompt))}
                        disabled={loading}
                    >
                        {t(cmd.label)}
                    </Button>
                ))}
            </div>

            {/* 消息列表 */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {messages.map((msg, i) => (
                    <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                        <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap flex flex-col gap-2 ${
                            msg.role === "user"
                                ? "bg-primary text-primary-foreground rounded-br-sm"
                                : "bg-muted/40 text-foreground rounded-bl-sm border"
                        }`}>
                            {msg.parts?.map((part, pIdx) => {
                                if (part.type === 'text') {
                                    return <div key={pIdx}>{part.content}</div>;
                                }
                                if (part.type === 'thinking') {
                                    return (
                                        <div key={pIdx} className="border-l-2 border-primary/20 pl-3 py-1 my-1 text-muted-foreground italic text-xs whitespace-pre-wrap">
                                            {part.content || (loading && t("components.memory.MemoryAgentChat.kd0522086"))}
                                        </div>
                                    );
                                }
                                if (part.type === 'tool_call') {
                                    return (
                                        <div key={pIdx} className="flex flex-col gap-1 bg-background/50 border rounded-md p-2 text-xs font-mono">
                                            <div className="flex items-center gap-1.5 text-primary">
                                                <Zap className="w-3 h-3" />
                                                <span className="font-semibold">{part.toolName}</span>
                                                {!part.isFinished && loading && <Loader2 className="w-3 h-3 animate-spin ml-auto" />}
                                                {part.isFinished && <div className="ml-auto text-green-500 text-[10px] border border-green-500/30 px-1 rounded">{t("components.memory.MemoryAgentChat.k1112c898")}</div>}
                                            </div>
                                            {part.toolArgs && typeof part.toolArgs === 'object' && Object.keys(part.toolArgs).length > 0 && (
                                                <div className="text-muted-foreground ml-4 border-l pl-2">
                                                    {Object.entries(part.toolArgs).map(([k, v]) => (
                                                        <div key={k} className="truncate"><span className="opacity-60">{k}:</span> {JSON.stringify(v)}</div>
                                                    ))}
                                                </div>
                                            )}
                                            {part.result && (
                                                <div className="text-muted-foreground mt-1 ml-4 border-l pl-2 pt-1 border-t-0 line-clamp-3">
                                                    {t("components.memory.MemoryAgentChat.kc8f7f305")}: {String(part.result)}
                                                </div>
                                            )}
                                        </div>
                                    );
                                }
                                return null;
                            })}

                            {(msg.parts?.length === 0 || (loading && i === messages.length - 1 && !msg.parts?.some(p => p.type === 'text' && p.content))) ? (
                                <span className="inline-flex gap-1 h-5 items-center">
                                    <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:0ms]" />
                                    <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:150ms]" />
                                    <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:300ms]" />
                                </span>
                            ) : null}
                        </div>
                    </div>
                ))}
                <div ref={bottomRef} />
            </div>

            {/* 输入框 */}
            <div className="border-t p-3 flex gap-2">
                <Input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder={t("components.memory.MemoryAgentChat.kbc7a3903")}
                    onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage(input)}
                    disabled={loading}
                    className="text-sm"
                />
                <Button size="icon" onClick={() => sendMessage(input)} disabled={loading || !input.trim()}>
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                </Button>
            </div>
        </div>
    );
}
