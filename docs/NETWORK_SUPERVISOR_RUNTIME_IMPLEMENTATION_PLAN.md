# NETWORK SUPERVISOR RUNTIME

## What this runtime does

`NETWORK SUPERVISOR RUNTIME` is the networking layer that lets one V8 node discover, trust, wake, and explicitly delegate work to another V8 node.

The first release keeps the model simple:

- LAN discovery through UDP multicast
- WAN bootstrap through configured peers
- explicit trust through peer tokens and public keys
- directed wake with acknowledgement
- explicit remote delegation with `peerId + task`
- local run truth preserved through `runtime_events`, `workflow_ledger`, and run metadata

This runtime does **not** replace the local `chat` runtime. It wraps remote collaboration around the existing execution plane.

## The reader's mental model

Think of the network as a small team of V8 nodes:

- every node has its own local runtimes, memory, ledger, and tools
- one node can discover another
- one node can trust another with clear boundaries
- one node can wake another
- one node can ask another to execute a child task
- the local node still owns the main run story

That is why the network is:

- distributed for execution
- local-first for truth

## First release scope

The first release is intentionally narrow:

- **yes** to discovery
- **yes** to challenge / join
- **yes** to directed wake
- **yes** to explicit delegation
- **no** to automatic peer routing
- **no** to broker-first task queues
- **no** to a full topology console

## Protocol model

Every network message uses a versioned JSON envelope with these fields:

- `version`
- `messageId`
- `messageType`
- `sentAt`
- `expiresAt`
- `fromPeerId`
- `toPeerId`
- `nonce`
- `signature`
- `trace`
- `payload`

`trace` must carry enough local context to reconnect the network event to the local runtime story:

- `sourceRunId`
- `sourceSessionId`
- `workflowId`
- `delegationId`

## Security model

The first release uses:

- Ed25519 for node identity and envelope signing
- HTTPS / WSS for transport
- peer token + challenge / response for enrollment
- nonce + timestamp + expiry for freshness checks

Discovery is not trust.

Finding a peer only means:

- the node exists
- it announced reachable endpoints
- the packet signature was valid

Trust still requires a join / challenge flow and explicit peer registration.

## Remote delegation model

The delegation flow is:

1. local node chooses a trusted peer
2. local node creates an outer `network_supervisor` run
3. local node sends `delegation.request`
4. remote node verifies trust and creates an outer `network_supervisor` run
5. remote node creates an inner local `chat` run
6. remote node streams `accepted / progress / result / failed`
7. local node projects that state back into its own ledger and runtime events

The key rule is simple:

> Remote execution is allowed. Remote truth is not.

The local node still owns the reader-facing run story.

## Admin page responsibilities

The Admin page for `NETWORK SUPERVISOR RUNTIME` should answer five questions:

1. Is the runtime enabled?
2. Which peers are discovered?
3. Which peers are trusted?
4. Can this node currently delegate?
5. Can I challenge, wake, and delegate from one place?

The first release page is a **configuration + status + diagnostics** page, not a topology dashboard.

## Final takeaway

`NETWORK SUPERVISOR RUNTIME` turns V8 from a single-node agent OS into a secure multi-node Supervisor network.

It must do five things well:

1. discover peers
2. trust peers safely
3. wake peers directly
4. delegate work across nodes
5. keep local runtime truth intact
