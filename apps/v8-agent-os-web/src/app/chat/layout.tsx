import { Sidebar } from "@/components/layout/Sidebar";
import { ConversationProvider } from "@/context/ConversationContext";

export default function ChatLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <ConversationProvider>
            <div className="flex h-full min-h-0 w-full overflow-hidden">
                <Sidebar />
                <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
                    {children}
                </div>
            </div>
        </ConversationProvider>
    );
}
