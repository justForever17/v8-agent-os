import { NextRequest } from "next/server";
import { proxyConversationMutation } from "@/lib/server/proxy/conversation-mutation";
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    return proxyConversationMutation(req, (await params).id);
}
