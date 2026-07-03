const RELAY_PROTOCOL_VERSION = "v8-relay.v1";
const DEFAULT_TTL_SECONDS = 300;
const MAX_LIMIT = 200;

function json(payload, init = {}) {
  return new Response(JSON.stringify(payload), {
    ...init,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...(init.headers || {}),
    },
  });
}

function notFound() {
  return json({ ok: false, error: "not_found" }, { status: 404 });
}

function relayDescriptor() {
  return {
    ok: true,
    protocolVersion: RELAY_PROTOCOL_VERSION,
    protocol: {
      version: RELAY_PROTOCOL_VERSION,
      delivery: ["rendezvous", "mailbox", "websocket"],
      messageStates: ["queued", "delivered", "acked", "expired", "dead_letter"],
      mailboxTruth: "durable_object_storage",
      queueRole: "retry_delay_dead_letter_only",
      wireEnvelope: "network_supervisor.signed_envelope",
    },
  };
}

function targetPeerFromPublishBody(body) {
  return String(body?.targetPeerId || body?.envelope?.toPeerId || body?.envelope?.to_peer_id || "").trim();
}

function roomForPeer(env, peerId) {
  const id = env.V8_RELAY_ROOM.idFromName(String(peerId || "").trim());
  return env.V8_RELAY_ROOM.get(id);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/.well-known/v8-relay") {
      return json(relayDescriptor());
    }

    if (request.method === "POST" && url.pathname === "/v1/relay/publish") {
      const body = await request.json().catch(() => ({}));
      const peerId = targetPeerFromPublishBody(body);
      if (!peerId) return json({ ok: false, error: "missing_target_peer_id" }, { status: 400 });
      return roomForPeer(env, peerId).fetch(new Request(request.url, {
        method: "POST",
        headers: request.headers,
        body: JSON.stringify(body),
      }));
    }

    if (request.method === "GET" && url.pathname.startsWith("/v1/relay/mailbox/")) {
      const peerId = decodeURIComponent(url.pathname.slice("/v1/relay/mailbox/".length));
      if (!peerId) return json({ ok: false, error: "missing_peer_id" }, { status: 400 });
      return roomForPeer(env, peerId).fetch(request);
    }

    if (request.method === "POST" && url.pathname === "/v1/relay/ack") {
      const body = await request.json().catch(() => ({}));
      const peerId = String(body?.peerId || "").trim();
      if (!peerId) return json({ ok: false, error: "missing_peer_id" }, { status: 400 });
      return roomForPeer(env, peerId).fetch(new Request(request.url, {
        method: "POST",
        headers: request.headers,
        body: JSON.stringify(body),
      }));
    }

    if (request.method === "GET" && url.pathname === "/v1/relay/ws") {
      const peerId = String(url.searchParams.get("peerId") || "").trim();
      if (!peerId) return json({ ok: false, error: "missing_peer_id" }, { status: 400 });
      return roomForPeer(env, peerId).fetch(request);
    }

    return notFound();
  },

  async queue(batch, env) {
    // Optional retry/DLQ auxiliary path. The mailbox source of truth remains in
    // Durable Object storage; Queue is for delayed processing or Cloudflare DLQ.
    for (const message of batch.messages) {
      try {
        const body = message.body || {};
        const peerId = String(body.peerId || "").trim();
        if (peerId) {
          await roomForPeer(env, peerId).fetch("https://relay.internal/v1/relay/dead-letter", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
          });
        }
        message.ack();
      } catch (error) {
        message.retry();
      }
    }
  },
};

export class V8RelayRoom {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.sessions = new Set();
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/v1/relay/publish") {
      return this.publish(request);
    }
    if (request.method === "GET" && url.pathname.startsWith("/v1/relay/mailbox/")) {
      return this.mailbox(url);
    }
    if (request.method === "POST" && url.pathname === "/v1/relay/ack") {
      return this.ack(request);
    }
    if (request.method === "GET" && url.pathname === "/v1/relay/ws") {
      return this.websocket(request);
    }
    if (request.method === "POST" && url.pathname === "/v1/relay/dead-letter") {
      return this.deadLetter(request);
    }
    return notFound();
  }

  async publish(request) {
    const body = await request.json().catch(() => ({}));
    const envelope = body.envelope || {};
    if (!envelope.messageId && !envelope.message_id) {
      return json({ ok: false, error: "missing_envelope_message_id" }, { status: 400 });
    }
    const ttlSeconds = Math.max(60, Number(body.ttlSeconds || DEFAULT_TTL_SECONDS));
    const now = Date.now();
    const seq = Number((await this.ctx.storage.get("seq")) || 0) + 1;
    const relayMessageId = `nrelay_${crypto.randomUUID().replaceAll("-", "")}`;
    const record = {
      relayMessageId,
      cursor: String(seq),
      state: "queued",
      idempotencyKey: String(body.idempotencyKey || envelope.messageId || envelope.message_id || ""),
      envelope,
      createdAt: new Date(now).toISOString(),
      expiresAt: new Date(now + ttlSeconds * 1000).toISOString(),
      deliveredAt: null,
      ackedAt: null,
    };
    await this.ctx.storage.put(`msg:${seq}`, record);
    await this.ctx.storage.put(`msgid:${relayMessageId}`, String(seq));
    await this.ctx.storage.put("seq", seq);
    this.broadcast({ type: "relay.message", relayMessageId, cursor: String(seq) });
    return json({ ok: true, relayMessageId, state: "queued", cursor: String(seq) });
  }

  async mailbox(url) {
    const cursor = Number(url.searchParams.get("cursor") || 0);
    const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit") || 50), MAX_LIMIT));
    const seq = Number((await this.ctx.storage.get("seq")) || 0);
    const items = [];
    let nextCursor = String(cursor || 0);
    for (let index = cursor + 1; index <= seq && items.length < limit; index += 1) {
      const key = `msg:${index}`;
      const record = await this.ctx.storage.get(key);
      if (!record) continue;
      if (record.state === "acked" || record.state === "dead_letter") {
        nextCursor = String(index);
        continue;
      }
      if (Date.parse(record.expiresAt) <= Date.now()) {
        record.state = "expired";
        await this.ctx.storage.put(key, record);
        await this.sendDeadLetter({ ...record, reason: "expired" });
        nextCursor = String(index);
        continue;
      }
      record.state = "delivered";
      record.deliveredAt = record.deliveredAt || new Date().toISOString();
      await this.ctx.storage.put(key, record);
      items.push(record);
      nextCursor = String(index);
    }
    return json({ ok: true, items, nextCursor });
  }

  async ack(request) {
    const body = await request.json().catch(() => ({}));
    const messageIds = Array.isArray(body.messageIds) ? body.messageIds.map(String).filter(Boolean) : [];
    const acked = [];
    for (const relayMessageId of messageIds) {
      const seq = await this.ctx.storage.get(`msgid:${relayMessageId}`);
      if (!seq) continue;
      const key = `msg:${seq}`;
      const record = await this.ctx.storage.get(key);
      if (!record) continue;
      record.state = "acked";
      record.ackedAt = new Date().toISOString();
      await this.ctx.storage.put(key, record);
      acked.push(relayMessageId);
    }
    return json({ ok: true, acked });
  }

  async websocket(request) {
    if (request.headers.get("upgrade") !== "websocket") {
      return json({ ok: false, error: "expected_websocket" }, { status: 426 });
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    if (this.ctx.acceptWebSocket) {
      this.ctx.acceptWebSocket(server);
    } else {
      server.accept();
      this.sessions.add(server);
      server.addEventListener("close", () => this.sessions.delete(server));
      server.addEventListener("error", () => this.sessions.delete(server));
    }
    server.send(JSON.stringify({ type: "relay.ready", protocolVersion: RELAY_PROTOCOL_VERSION }));
    return new Response(null, { status: 101, webSocket: client });
  }

  async deadLetter(request) {
    const body = await request.json().catch(() => ({}));
    const seq = Number((await this.ctx.storage.get("deadLetterSeq")) || 0) + 1;
    await this.ctx.storage.put("deadLetterSeq", seq);
    await this.ctx.storage.put(`dead:${seq}`, {
      ...body,
      state: "dead_letter",
      createdAt: new Date().toISOString(),
    });
    return json({ ok: true, deadLetterId: `dead:${seq}` });
  }

  async sendDeadLetter(record) {
    if (this.env.RELAY_RETRY_QUEUE) {
      await this.env.RELAY_RETRY_QUEUE.send(record);
    } else {
      await this.deadLetter(new Request("https://relay.internal/v1/relay/dead-letter", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(record),
      }));
    }
  }

  broadcast(payload) {
    const text = JSON.stringify(payload);
    if (this.ctx.getWebSockets) {
      for (const socket of this.ctx.getWebSockets()) {
        try {
          socket.send(text);
        } catch {
          // Hibernated sockets are owned by the platform; ignore stale handles.
        }
      }
    }
    for (const socket of this.sessions) {
      try {
        socket.send(text);
      } catch {
        this.sessions.delete(socket);
      }
    }
  }
}
