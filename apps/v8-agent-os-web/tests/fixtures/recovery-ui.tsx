import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ConversationRecoveryActions } from "@/components/chat/ConversationRecoveryActions";
import { LocaleProvider } from "@/components/providers/LocaleProvider";
import { hydrateDraft } from "@/lib/composer-drafts";
import type { RecoveryMessage } from "@v8/session-realtime";

const draftKey = JSON.stringify(["isolated-test", "owner", "workspace", "s"]);
function Harness() {
    const [snapshot, setSnapshot] = useState<any>(null);
    const load = async () => setSnapshot(await (await fetch("/api/conversations/s")).json());
    useEffect(() => { void hydrateDraft(draftKey).then(load); }, []);
    (window as any).refreshFixture = load;
    if (!snapshot) return <div>Loading</div>;
    const visibleMessages = new URLSearchParams(location.search).has("around") ? snapshot.messages.slice(0, 1) : snapshot.messages;
    return <LocaleProvider initialLocale="en"><main style={{ maxWidth: 680, margin: "32px auto", padding: 16 }}>
        <h1>Conversation recovery — isolated component fixture</h1>
        {visibleMessages.map((message: RecoveryMessage, index: number) => <section key={message.id} data-message-id={message.id} style={{ borderBottom: "1px solid #ddd", padding: 16 }}>
            <pre style={{ whiteSpace: "pre-wrap" }}>{message.content}</pre>
            <ConversationRecoveryActions message={message} turnEnd={snapshot.messages[index + 1]?.turnId !== message.turnId}
                hasDescendants={index < visibleMessages.length - 1} laterTurnCount={new Set(visibleMessages.slice(index + 1).map((item: RecoveryMessage) => item.turnId)).size}
                recovery={{ sessionId: "s", draftKey, transcriptRevision: snapshot.transcriptRevision, busy: snapshot.busy, onCommitted: load }} />
        </section>)}
    </main></LocaleProvider>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
