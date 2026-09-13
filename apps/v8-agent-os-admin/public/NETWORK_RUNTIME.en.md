# Network Runtime guide

Network Supervisor Runtime connects multiple V8 Agent OS nodes through observable, approvable, and recoverable collaboration. It is not a raw remote shell, and it never changes VPN, route, DNS, or firewall settings automatically.

## Start collaborating between two devices

Both devices must run V8OS. Phone pairing is a separate connection flow; signing in on a phone does not make it a task execution peer.

1. Open Network Runtime in Admin on both devices and enable device collaboration.
2. Under “Device connection”, choose a detected Admin address, such as `http://192.168.1.10:9528`. Across networks, use a signed-in Tailscale address or a stable HTTPS tunnel to Admin.
3. Click “Check connection”, then “Save address”. This checks the route from the current device only; complete pairing to verify the other device. A route check is not task completion.
4. Generate a code on one device and copy its connection invite. Paste it on the other device, confirm that it came from a device you trust, and click “Trust and connect”. The invite contains a code, address, public key and expiry, not a peer token. It works without multicast discovery and requires no manual peer ID, public key or secret fields.
5. Select the connected device and optionally set “Local execution directory”. Incoming tasks run there on this machine; this does not set or expose the other machine's actual directory.
6. Describe a task, choose the device and send it. Check the task result: “Received by device” proves message delivery, not completed execution.

Generate a new invite when it expires. Discovered LAN devices can still use “Use a code with a discovered device”. Discovery alone does not establish trust.

## Address and authentication

- Normally configure one **Admin origin**. Engine may keep listening on loopback; ordinary Engine APIs do not need direct LAN or public exposure.
- Admin's `/v1/network-supervisor/peer/*` is a restricted device HTTP proxy. Engine independently checks peer tokens, signatures, target identity and expiry; pairing additionally checks the short code. It does not require or forward the browser's Admin cookie.
- Do not advertise `127.0.0.1`, `localhost` or `0.0.0.0` to another device. Suggested addresses have not been verified from the other device; multiple adapters, VPNs and firewalls can affect reachability.
- LAN discovery requires multicast on the same network. Tailscale/Headscale usually do not forward it; use a connection invite. V8 does not alter VPN routes, DNS, MTU or firewall settings.
- This Admin proxy supports HTTP, not WebSocket upgrades. A separately hosted static Web page is not a peer or WebSocket server either. The advanced WS address is only for a separately configured direct Engine WebSocket route; normal neighbor pairing, messages and tasks do not require it.

## Headscale access

Headscale is supported as an optional self-hosted mesh control plane.

1. Set the Headscale control URL in Remote Link.
2. Enter the API key in Admin. The key is stored only in the Engine Secret Store and never goes into config.json, logs, ToolMessages, or model context.
3. Use connection test to inspect users, nodes, and preauth keys.
4. Create a short-TTL single-use preauth key when a new node needs to join.
5. Route, exit node, ACL, node delete, and node expire operations stay in Admin and require explicit confirmation.

Agents do not get raw Headscale management tools.

## Phone connection

Phone keeps its own scan/paste pairing flow and connection profiles. A neighbor invite cannot sign in a Phone, and Phone credentials are not peer tokens. Sharing a LAN or VPN does not automatically grant trust.

## Cloudflare Tunnel versus Relay

**Tunnel** forwards a stable HTTPS domain, such as `https://v8.example.com`, to local Admin. The other device still calls this node's peer routes directly; no mailbox is added. If Cloudflare Access blocks that route, a browser login does not authenticate peer requests. The user must configure the peer route policy while retaining V8's signed device authentication. Temporary `trycloudflare.com` URLs are not used for persistent device invites.

**V8 Relay** is a separate mailbox service reached by both nodes. It helps devices that cannot connect directly, does not replace pairing, and its Worker URL must not be mistaken for an Admin origin.

## Public connection / V8 Relay

V8 Relay carries neighbor messages when two devices are not on the same LAN or mesh network. It is not a Phone login code, and it is not the OpenAI or Anthropic compatible API. It only forwards V8 signed envelopes; trust is still enforced by short-code pairing, peer tokens, public keys, nonces, expiry, and local Safety.

### When to use it

- Two V8 devices cannot connect directly, but both can reach the same public relay.
- You want temporary offline storage within the message's validity period, without a continuously connected WebSocket.

If LAN, Tailscale, or Headscale is already reliable, prefer direct connection.

### How delivery works

1. When Engine sends a neighbor message, it first writes to local `network_relay_outbox`.
2. Relay Transport calls the active adapter’s `POST /v1/relay/publish`.
3. Relay Worker stores the signed envelope in the target device mailbox.
4. The target Engine incrementally pulls `GET /v1/relay/mailbox/{peerId}?cursor=...`.
5. The target Engine verifies the envelope and hands it to the neighbor message pool.
6. After successful processing, it calls `POST /v1/relay/ack`.
7. WebSocket is only an online push hint; scheduled pull still recovers messages after disconnects.

### Cloudflare adapter preparation

Prepare these pieces in your own Cloudflare account:

- Worker: public HTTP / WebSocket ingress.
- Durable Object: stateful coordination and mailbox indexes for each peer mailbox / room.
- Durable Object storage: pullable messages, cursors, and ACK state.
- Queue: delayed retry and dead-letter handling only; it is not the mailbox source of truth.
- Optional custom domain: the public Relay URL.

### Deployment templates

The Engine repository provides templates:

- `apps/v8-agent-os-engine/runtimes/network_supervisor/relay_templates/cloudflare_worker.mjs`
- `apps/v8-agent-os-engine/runtimes/network_supervisor/relay_templates/wrangler.toml.example`

Recommended flow:

1. Copy the templates into a Cloudflare Worker project.
2. Create Durable Object and Queue bindings according to `wrangler.toml.example`.
3. Deploy the Worker with Wrangler.
4. Open `https://<relay-domain>/.well-known/v8-relay`; it should report `v8-relay.v1`.
5. Return to the Admin “Public connection (V8 Relay)” card and choose Cloudflare Relay.
6. Fill in the public Relay URL, Worker name, Queue name, and Durable Object namespace.
7. Save the configuration.
8. Both devices still need connection-invite pairing through a reachable route first; Relay does not establish trust automatically.

### Self-hosted adapter

When “Self-hosted Relay” is selected, the service only needs to implement the same HTTP / WebSocket endpoints:

- `GET /.well-known/v8-relay`
- `POST /v1/relay/publish`
- `GET /v1/relay/mailbox/{peerId}?cursor=...&limit=...`
- `POST /v1/relay/ack`
- `GET /v1/relay/ws?peerId=...`

### Verification

- Admin status shows Relay as ready.
- `queued` means waiting to send locally; `published` means accepted by the relay only. Neither proves receipt by the target device or task completion.
- The target device sees the message in the neighbor conversation timeline.
- After target ACK, the Relay mailbox does not redeliver the message.
- After WebSocket disconnects, scheduled pull can still receive messages.

Offline storage is bounded by both Relay TTL and the signed envelope's `expiresAt`, whichever is shorter. Current delivery does not guarantee execution after arbitrarily long offline periods. Expiry must remain an explicit failure; inspect the other device's outcome before deciding to resend. Extending an old signature is not a valid recovery.

### Common errors

- `relay_disabled`: Relay is not enabled.
- `runtime_disabled`: Network Runtime is disabled.
- `active_adapter_not_configured`: the active adapter has no public Relay URL.
- `missing_target_peer_id`: publish request has no target peer.
- `Envelope signature verification failed`: pairing, public keys, or message integrity failed.
- Message enters dead-letter: expired, malformed, unpaired target, or repeated failures.

### Safety boundaries

- Relay only forwards signed envelopes; it cannot establish trust for a device.
- Relay does not execute local files, shell commands, or workspace paths.
- Remote `workspacePath` is only source metadata; executable paths are resolved by the local workspace resolver.
- Cloudflare tokens are not saved into V8 config; the Admin Relay card only stores public ingress and adapter metadata.

## External compatible APIs

Network Runtime exposes OpenAI and Anthropic compatible endpoints:

```text
/api/network-supervisor/openai/v1/chat/completions
/api/network-supervisor/anthropic/v1/messages
```

These endpoints go through the Admin relay. External tools remain external; V8 does not silently replace them with local file or shell tools.

Enable the compatible API in the unified access card, create an access key, and copy the displayed address and key into your client. A neighbor invitation or Admin password is not an API key.

Clients that support pauses can inspect `v8os_run` in the response. `waiting_input` needs an answer; `waiting_approval` needs approval in V8OS. Neither means completion. With the same access key, POST this optional extension to the original protocol endpoint to inspect the existing run without replaying the task:

```json
{"v8os_control":{"runId":"runId from the response","action":"status"}}
```

Other actions are `answer` (with `interactionId` and a nonempty `answer`) and `cancel`. An API key cannot use this extension to self-approve local Safety operations. Tool results must retain the original call ID and thread/session identity; mismatched identities and consumed results are rejected.

## Local ACP access

Start V8OS and complete Admin initialization, then configure an ACP editor to launch `v8os acp`. It authenticates with the local Admin without copying an API key. Use `V8OS_ADMIN_URL` for a different local port; automatic authentication currently requires a loopback address.

The editor's selected directory becomes the session's trusted local project without changing the global workspace. Text, embedded text resources, streaming, session loading, cancellation, questions and permissions are supported. Unsupported image/audio input and external MCP lists produce explicit errors. The editor must implement the corresponding permission and elicitation capabilities; a paused task must not be presented as complete.

## Artifact preview

Artifacts follow the current connection entrypoint.

- If Phone uses LAN, artifact URLs use the LAN Admin origin.
- If Phone uses Tailscale or Headscale, artifact URLs use the current mesh Admin origin.
- Content still goes through the Admin client artifact proxy:

```text
/api/client/artifacts/{artifactId}/content?sessionId={sessionId}
```

`sessionId` is a required resource-authority boundary; requests that omit it or do not match the current Session/Workspace are rejected. Short-lived signed URLs cover the full path and this query parameter.

V8 does not globally rewrite LAN artifact links to mesh URLs because a mesh profile exists.

## Common actions

- Copy compat URL: use with OpenAI or Anthropic compatible clients.
- Copy connection invite: pair another V8OS device after explicit confirmation.
- Check connection: test the peer HTTP route from this device; this does not replace pairing or task acceptance.
- Load earlier messages: read history beyond the latest 100 entries.
- Retry delivery: resend the same message identity without creating a new task. Inspect the other device first if execution may already have happened.

## Troubleshooting

### Phone cannot open Admin

- Check whether the phone and Admin are on the same LAN or mesh.
- Do not use `127.0.0.1` or `localhost` from Phone.
- In Tailscale mode, confirm both devices are online.
- WireGuard full-tunnel can override DNS or routes. V8 only reports the risk and does not change the configuration.

### Peer is not found

- Confirm Network Runtime is enabled.
- LAN discovery requires multicast on the same network.
- Mesh candidates only mean the node is visible on the network; it is not a trusted V8 peer yet.
- Paste a connection invite when no candidate is listed; multicast discovery is not required.

### Challenge fails

- Check the other device's Admin origin, listener, firewall and Tunnel peer route.
- Use matching versions on both devices. If identity changed, revoke the old connection and pair again instead of manually replacing an unknown key.
- Inspect the failure class: `peer_unreachable`, `route_conflict`, `auth_failed`, or `mesh_provider_unconfigured`.

### Artifact preview fails

- Confirm the current Phone Admin origin is reachable.
- Confirm the session is authenticated.
- Reopen the artifact through the current connection entrypoint; do not mix LAN pages with mesh URLs or the reverse.

## Safety boundaries

- V8 does not install VPN clients.
- V8 does not mutate WireGuard or Tailscale routes, DNS, MTU, or keys.
- Headscale API keys stay in the Secret Store.
- Candidate nodes are never trusted automatically.
- Remote delegation must pass token, public key, challenge, and Safety boundaries.
