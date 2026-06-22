type VoiceEntry = {
  value: string;
  label: string;
};

function voiceListUrlFromTtsEndpoint(endpoint: string): string {
  if (!endpoint) return "https://api.minimaxi.com/v1/get_voice";
  if (endpoint.includes("/v1/t2a_v2")) return endpoint.replace(/\/v1\/t2a_v2.*$/, "/v1/get_voice");
  if (endpoint.endsWith("/")) return `${endpoint.replace(/\/$/, "")}/v1/get_voice`;
  return `${endpoint.replace(/\/$/, "")}/v1/get_voice`;
}

function flattenMiniMaxVoices(payload: unknown): VoiceEntry[] {
  if (!payload || typeof payload !== "object") return [];
  const source = payload as Record<string, unknown>;
  const groups = ["system_voice", "voice_cloning", "voice_generation"];
  const voices: VoiceEntry[] = [];
  for (const group of groups) {
    const items = Array.isArray(source[group]) ? source[group] as Record<string, unknown>[] : [];
    for (const item of items) {
      const voiceId = typeof item.voice_id === "string" ? item.voice_id : "";
      if (!voiceId) continue;
      const name = typeof item.voice_name === "string" && item.voice_name ? item.voice_name : voiceId;
      voices.push({ value: voiceId, label: name });
    }
  }
  return voices;
}

export async function POST(req: Request) {
  try {
    const body = await req.json().catch(() => ({}));
    const protocol = String(body?.protocol || "");
    const apiKey = String(body?.apiKey || body?.api_key || "");
    const endpoint = String(body?.endpoint || "");

    if (protocol !== "minimax_t2a_v2") {
      return Response.json({ voices: [] });
    }
    if (!apiKey) {
      return Response.json({ error: "MiniMax API key is required." }, { status: 400 });
    }

    const response = await fetch(voiceListUrlFromTtsEndpoint(endpoint), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ voice_type: "all" }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const source = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
      const baseResp = source.base_resp && typeof source.base_resp === "object" ? source.base_resp as Record<string, unknown> : {};
      const message = typeof baseResp.status_msg === "string" ? baseResp.status_msg : `HTTP ${response.status}`;
      return Response.json({ error: message }, { status: response.status });
    }
    return Response.json({ voices: flattenMiniMaxVoices(payload) });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return Response.json({ error: message }, { status: 500 });
  }
}
