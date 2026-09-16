import { NextRequest } from "next/server";
import { proxyClientMedia } from "@/lib/server/client-identity-proxy";
export const runtime = "nodejs";
export async function POST(req: NextRequest) { return proxyClientMedia(req, "/user-background-upload"); }
