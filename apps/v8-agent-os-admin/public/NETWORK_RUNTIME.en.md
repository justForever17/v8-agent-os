# Network Runtime guide

Network Supervisor Runtime connects multiple V8 Agent OS nodes through observable, approvable, and recoverable collaboration. It is not a raw remote shell, and it never changes VPN, route, DNS, or firewall settings automatically.

## Terms

- **Connection profile**: the route Phone, Admin, Engine, or peers use to reach this node, such as LAN, Tailscale, Headscale, or a manual URL.
- **Recommended URL**: a URL V8 derives from current diagnostics. It is only a suggestion and is never applied automatically.
- **Candidate node**: a node discovered through LAN, Tailscale, or Headscale. It can fill the peer form but cannot become trusted automatically.
- **Trusted node**: a V8 node that has passed token, public key, and challenge checks.
- **One-time join key**: a short-lived single-use Headscale preauth key. It is shown once.

## LAN access

LAN is the default stable path. If Phone and Admin/Engine are on the same network, prefer LAN.

1. Confirm the Admin URL is reachable from the phone, for example:

```text
http://192.168.1.10:9528
```

2. Save that URL as a Phone connection profile.
3. Keep LAN discovery enabled in Network Runtime if you want local peer discovery.
4. Ignore Mesh Providers entirely if you do not need Tailscale, Headscale, or WireGuard.

The LAN profile is not downgraded or replaced just because Tailscale is available.

## Tailscale access

Tailscale is useful when you want to reach Admin/Engine across networks.

1. Log in to Tailscale on the machine running V8 Engine/Admin.
2. Refresh diagnostics in Remote Link or Network Runtime.
3. Copy the recommended Tailscale Admin URL, for example:

```text
http://your-node.tailnet.ts.net:9528
```

4. Add it as a separate Phone connection profile.

V8 only reads Tailscale state and recommends URLs. It does not switch the active profile automatically and does not change routes, DNS, MTU, or keys.

## Headscale access

Headscale is supported as an optional self-hosted mesh control plane.

1. Set the Headscale control URL in Remote Link.
2. Enter the API key in Admin. The key is stored only in the Engine Secret Store and never goes into config.json, logs, ToolMessages, or model context.
3. Use connection test to inspect users, nodes, and preauth keys.
4. Create a short-TTL single-use preauth key when a new node needs to join.
5. Route, exit node, ACL, node delete, and node expire operations stay in Admin and require explicit confirmation.

Agents do not get raw Headscale management tools.

## Phone connection

Phone consumes connection profiles and the link manifest.

- Same network: LAN URL.
- Remote access: Tailscale or Headscale URL.
- Temporary debugging: manual URL.

All remote connections still require normal V8 authentication. Being inside a mesh network is not enough to become trusted.

## Candidate to trusted peer

Tailscale and Headscale nodes appear as **candidate nodes**, not trusted peers.

Flow:

1. Click “Fill peer form” on a candidate.
2. Add peer token, public key, allowed scopes, and allowed workspaces.
3. Save the peer.
4. Run challenge.
5. Only after a successful challenge should the node be treated as trusted for wake and delegation.

Phone nodes are marked as requiring approval because a regular phone is not automatically a V8OS peer. It needs dedicated V8 Phone peer support plus token, public key, and challenge success.

## External compatible APIs

Network Runtime exposes OpenAI and Anthropic compatible endpoints:

```text
/api/network-supervisor/openai/v1/chat/completions
/api/network-supervisor/anthropic/v1/messages
```

These endpoints go through the Admin relay. External tools remain external; V8 does not silently replace them with local file or shell tools.

## Artifact preview

Artifacts follow the current connection entrypoint.

- If Phone uses LAN, artifact URLs use the LAN Admin origin.
- If Phone uses Tailscale or Headscale, artifact URLs use the current mesh Admin origin.
- Content still goes through the Admin client artifact proxy:

```text
/api/client/artifacts/{artifactId}/content
```

V8 does not globally rewrite LAN artifact links to mesh URLs because a mesh profile exists.

## Common actions

- Copy compat URL: use with OpenAI or Anthropic compatible clients.
- Copy recommended peer URL: use as another node’s peerBaseUrl.
- Test connection: check whether Admin, Engine, or a peer is reachable.
- Fill peer form from candidate: copies fields only; it does not trust the peer.
- Challenge: verifies token, public key, and route.
- Wake: wakes a trusted peer.
- Delegate: delegates a task to a trusted peer.

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

### Challenge fails

- Check peer token.
- Check public key.
- Check whether peerBaseUrl is reachable from the current node.
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
