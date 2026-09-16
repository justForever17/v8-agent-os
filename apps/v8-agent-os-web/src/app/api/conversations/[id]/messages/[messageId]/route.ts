import { NextRequest } from "next/server";
import { proxyConversationMutation } from "@/lib/server/proxy/conversation-mutation";
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string; messageId: string }> }) {
    const { id, messageId } = await params;
    return proxyConversationMutation(req, id, messageId);
}
