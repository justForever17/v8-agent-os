import { NextRequest } from "next/server";
import { proxyClientMedia } from "@admin/lib/server/client-identity-proxy";
export const runtime = "nodejs";
export async function POST(req: NextRequest) { return proxyClientMedia(req, "/user-avatar-upload"); }
