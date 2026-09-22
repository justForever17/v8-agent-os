import { NextRequest } from "next/server";
import { mutateConversation } from "@admin/lib/server/conversation-mutation";
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string; messageId: string }> }) {
    const { id, messageId } = await params;
    return mutateConversation(req, id, messageId);
}
