# Engine API Reference

This file lists the API surfaces readers usually need first.

It is not a dump of every internal route.

## Health

Use these first:

- `GET /health`
- `GET /v1/health`
- `GET /v1/extensions/health`
- `GET /v1/plugin-host`

These tell you whether the main runtime chain is alive.

## Chat and execution

Main entry:

- `POST /chat`

Use this when you want to send a normal task or conversation request into Engine.

## Config registry

Use:

- `GET /config-registry`
- `GET /config-registry/{domain}`
- `POST /config-registry/{domain}`

This is the preferred structured config surface for Admin.

Examples of active domains:

- `models`
- `memory`
- `plugin-host`
- `automation-runtime`
- `music`
- `system-base`

## Desktop live

Current routes:

- `GET /desktop-live/status`
- `POST /desktop-live/session`
- `POST /desktop-live/offer`
- `POST /desktop-live/candidate`
- `GET /desktop-live/stream`
- `DELETE /desktop-live/session/{session_id}`

These are still active and are used through the Admin/Web proxy chain.

## Plugin Host

Current entry family:

- `/v1/plugin-host/*`

Use this for:

- bridge status
- tool inventory
- channel and plugin runtime integration

## What not to treat as the main API

Avoid extending historical compatibility routes just because they are convenient.

If a new UI or control surface needs structured configuration or runtime status, add it to the active registry or runtime APIs instead.
