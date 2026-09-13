// Synthetic local HTTP fixture. It never reads application config or real tokens.
const http = require("node:http");
const port = Number(process.argv[2] || 22836);
const counts = { requests: 0, activeStreams: 0, maximumStreams: 0, submits: 0 };
const accepted = new Map();
const peers = Array.from({ length: 100 }, (_, i) => ({ linkId: `link-${i}`, peerId: `peer-${i}`, servingInstanceId: "fixture-A", sessionId: `network_neighbor_link-${i}`, sessionKind: "local_neighbor", displayName: `Supervisor ${String(i).padStart(3, "0")}`, online: i % 2 === 0, lastSeenAt: "2026-09-13T00:00:00Z", trustStatus: "trusted" }));
const timestamp = "2026-09-13T00:00:00Z";
const server = http.createServer(async (req, res) => {
  counts.requests++;
  res.setHeader("Access-Control-Allow-Origin", "*"); res.setHeader("Access-Control-Allow-Headers", "*"); res.setHeader("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS");
  if (req.method === "OPTIONS") { res.end(); return; }
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  let bytes = ""; for await (const chunk of req) bytes += chunk;
  let body; try { body = JSON.parse(bytes || "{}"); } catch { body = {}; }
  const parts = url.pathname.split("/"); const profile = parts[1] === "B" ? "B" : "A";
  const base = `http://127.0.0.1:${port}/${profile}`;
  const route = url.pathname.replace(/^\/[AB]/, "");
  const user = { id: "fixture-owner", login: "fixture", name: "Test owner", email: "fixture@invalid", role: "ADMIN" };
  const send = (payload, status = 200) => { res.statusCode = status; res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(payload)); };
  if (route === "/fixture/metrics") return send(counts);
  if (route.endsWith("/instance")) return send({ instanceId: `fixture-${profile}` });
  if (route.endsWith("/pairing/consume") || route.endsWith("/auth/refresh")) return send({ accessToken: `synthetic-access-${profile}`, refreshToken: `synthetic-refresh-${profile}`, user, instanceId: `fixture-${profile}`, adminBaseUrl: base, adminUrls: [base] });
  if (route.endsWith("/auth/me")) return send({ user });
  if (route.endsWith("/auth/logout")) return send({ success: true });
  if (route.includes("/stream")) {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    counts.activeStreams++; counts.maximumStreams = Math.max(counts.maximumStreams, counts.activeStreams);
    res.write(`event: ready\ndata: {"seq":0}\n\n`);
    const timer = setInterval(() => res.write(`event: heartbeat\ndata: {"seq":0}\n\n`), 1000);
    res.on("close", () => { clearInterval(timer); counts.activeStreams--; }); return;
  }
  const sessions = Array.from({ length: 1000 }, (_, i) => ({ id: `session-${i + 1}`, sessionId: `session-${i + 1}`, title: `${profile} task ${i + 1}`, createdAt: timestamp, updatedAt: timestamp, historySortAt: timestamp,
    workspaceId: "fixture-workspace", workspacePath: "E:/fixture", workspaceDisplayName: "Fixture project", projectId: "fixture-project", supervisorRuntimeMode: "auto", status: "idle", source: "chat" }));
  if (route === "/api/client/conversations") {
    const all = sessions.filter((row) => row.title.toLowerCase().includes((url.searchParams.get("q") || "").toLowerCase()));
    if (!url.searchParams.has("limit")) return send(all);
    const offset = Number(url.searchParams.get("cursor") || 0), limit = Number(url.searchParams.get("limit") || 80);
    return send({ items: all.slice(offset, offset + limit), pageInfo: { nextCursor: offset + limit < all.length ? String(offset + limit) : null } });
  }
  if (route === "/api/client/supervisor-peers") {
    const filtered = peers.filter((peer) => peer.displayName.toLowerCase().includes((url.searchParams.get("q") || "").toLowerCase()));
    const offset = Number(url.searchParams.get("cursor") || 0);
    return send({ servingInstanceId: `fixture-${profile}`, items: filtered.slice(offset, offset + 40).map((peer) => ({ ...peer, servingInstanceId: `fixture-${profile}` })), nextCursor: offset + 40 < filtered.length ? String(offset + 40) : null });
  }
  if (route.includes("/supervisor-peers/") && route.endsWith("/timeline")) {
    const linkId = route.split("/").at(-2); const peer = peers.find((item) => item.linkId === linkId);
    return send({ peer, items: [{ id: "message-1", body: "Synthetic neighbor transcript", seq: 1, fromNickname: peer.displayName, status: "delivered" }], previousCursor: null });
  }
  if (route.endsWith("/scope")) return send({ sessionId: route.split("/").at(-2), binding: { resolvedScope: "workspace", projectId: "fixture-project", workspaceId: "fixture-workspace", workspacePath: "E:/fixture" }, resolvedScope: "workspace", projectId: "fixture-project", workspaceId: "fixture-workspace", workspacePath: "E:/fixture" });
  if (route.includes("/turns") || route.includes("/timeline/sync")) return send({ sessionId: route.split("/")[4], messages: [{ id: "message-1", role: "assistant", content: `${profile} synthetic message`, ordinal: 1, createdAt: timestamp, turnId: "turn-1" }], deletions: [], syncCursor: `cursor-${profile}`, pageInfo: { hasOlder: false } });
  if (route.endsWith("/processes")) return send({ processes: [], stale: false });
  if (route.includes("/snapshot") || /^\/api\/client\/conversations\/[^/]+$/.test(route)) return send({ conversation: sessions[0], session: sessions[0], runtime: { status: "idle", latestSeq: 0 }, messages: [], queuedMessages: [], controls: { canInterrupt: false }, latestSeq: 0, messagesOmitted: true });
  if (route.endsWith("/chat/submit") || route.endsWith("/chat")) {
    counts.submits++; if (!accepted.has(body.clientMessageId)) accepted.set(body.clientMessageId, { accepted: true, runId: "fixture-run", userMessage: { id: body.clientMessageId, role: "user", content: body.message || body.content || "fixture" } });
    setTimeout(() => send(accepted.get(body.clientMessageId)), 1500); return;
  }
  if (route.includes("/projects")) return send({ projects: [{ id: "fixture-project", name: "Fixture project", workspaceId: "fixture-workspace", workspacePath: "E:/fixture" }], mainWorkspacePath: "E:/fixture" });
  if (route.includes("/reasoning-effort")) return send({ levels: ["auto"], value: "auto", supported: false });
  if (route.includes("/skills")) return send({ skills: [], subagentFamilies: [] });
  return send([]);
});
server.listen(port, "127.0.0.1", () => console.log(`Synthetic Phone fixture listening on ${port}`));
