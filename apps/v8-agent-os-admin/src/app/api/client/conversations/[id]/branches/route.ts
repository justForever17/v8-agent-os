import { NextRequest } from "next/server";
import { mutateConversation } from "@/lib/server/conversation-mutation";
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    return mutateConversation(req, (await params).id);
}
