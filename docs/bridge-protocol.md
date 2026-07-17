# GrowAssistant Bridge Protocol Specification

This document specifies the over-the-wire contract between a **GrowAssistant
Bridge** instance (this repository) and the **GrowAssistant app**. The transport
is **MQTT**: the bridge and the app both connect to a shared broker; neither
connects directly to the other. A backend engineer should be able to implement
the app side (or a compatible bridge) by reading this document alone.

The **app side of this contract is the source of truth.** The bridge conforms to
the app's payload builders/parsers in `lib/bridge/{topics,manifest,telemetry,
commands,webrtc,turn,jwt,mqtt-auth}.ts` and `lib/automations/{publish,status,
schema}.ts` (paths relative to the app repo). Where this document cites bridge
source it uses the form `app/mqtt_transport.py:390` (relative to the bridge repo).

Devices are identified exclusively by `entityId` (`<domain>.<name>`). All legacy
REST/SSE transport (`api_client`, `/bridge/{id}/…` endpoints, the SSE event
stream) has been removed.

---

## Table of contents

- [1. Overview](#1-overview)
- [2. Identity & pairing (HTTPS bootstrap)](#2-identity--pairing-https-bootstrap)
- [3. Broker authentication & ACL](#3-broker-authentication--acl)
- [4. Topic scheme](#4-topic-scheme)
- [5. Connection lifecycle](#5-connection-lifecycle)
- [6. Manifest (`…/manifest`, retained)](#6-manifest-manifest-retained)
- [7. State (`…/state`, retained)](#7-state-state-retained)
- [8. Telemetry (`…/telemetry`)](#8-telemetry-telemetry)
- [9. Commands (`…/cmd/<id>` + `…/cmd/<id>/ack`)](#9-commands-cmdid--cmdidack)
- [10. Automations (`…/automations` + `…/automations/status`)](#10-automations-automations--automationsstatus)
- [11. WebRTC camera signalling (`…/webrtc/offer` + `…/webrtc/answer`)](#11-webrtc-camera-signalling-webrtcoffer--webrtcanswer)
- [12. Device classification (tags)](#12-device-classification-tags)
- [13. Stable identity rules](#13-stable-identity-rules)
- [14. Liveness model](#14-liveness-model)
- [15. Glossary](#15-glossary)
- [16. Open questions / pending contract notes](#16-open-questions--pending-contract-notes)

---

## 1. Overview

The bridge is a NAT-bound agent running inside the operator's grow room. It never
accepts inbound TCP connections. All communication is via a shared MQTT broker
that both the bridge and the app connect to as clients:

```
   ┌──────────────────────┐        ┌───────────────────┐        ┌──────────────┐
   │  Bridge (grow room)  │  MQTT  │   MQTT broker     │  MQTT  │ GrowAssistant│
   │  paho client         │◀──────▶│ (mosquitto +      │◀──────▶│ app          │
   │                      │        │  go-auth backend) │        │ (subscriber) │
   └──────────────────────┘        └───────────────────┘        └──────────────┘
            ▲                                                          │
            │ HTTPS bootstrap (pair / token / ice-servers)             │
            └──────────────────────────────────────────────────────────┘
```

- **The broker** is reachable on a public hostname. Both sides connect *out* to
  it, so the bridge needs no port-forwarding.
- **The app's subscriber** (`lib/bridge/subscriber.ts`) connects as a broker
  **superuser** and receives every tenant's bridge → app traffic.
- **A bridge** connects with `username = bridgeId`, `password = <JWT>`, and is
  ACL-scoped to its own `ga/<tenantId>/bridge/<bridgeId>/` subtree.

The bridge is the **source of truth for which devices physically exist** (the
manifest). The app is the **source of truth for desired state** (automations,
device classification, commands). Retained messages carry the last-known
desired and reported state across reconnects.

### Identity primitives

| Name        | Format             | Lifetime                                            | Source |
|-------------|--------------------|-----------------------------------------------------|--------|
| `bridgeId`  | UUID/cuid (string) | Stable for the bridge's install                     | Issued by the app at pairing, persisted in `data/credentials.json` (`app/auth.py`) |
| `tenantId`  | string             | Stable                                              | Issued by the app at pairing |
| `entityId`  | `<domain>.<name>`  | Stable across bridge restarts and registry rebuilds | Built by the shared `derive_entity_id` (`app/entity_id.py`); see §13 |

Everything else (`manifestVersion`, automations `version`, command `id`,
`sessionId`) is monotonic per-bridge state or per-request scratch.

---

## 2. Identity & pairing (HTTPS bootstrap)

MQTT credentials are bootstrapped over HTTPS — the only non-MQTT channel. The
pairing direction is app-issued: the app shows the operator a pairing code, the
operator enters it into the bridge's web UI, and the bridge claims it.

All three endpoints are POSTs to `api.url` (the app base URL).

### 2.1 `POST /api/bridge/pair` — claim a pairing code

Request:
```json
{"code": "ABC123", "name": "raspberrypi"}
```
`name` is this host's name (informational). Source: `app/auth.py` `pair_with_code`.

Response (200):
```json
{
  "bridgeId": "…",
  "tenantId": "…",
  "bridgeSecret": "…",
  "token": "<JWT>",
  "tokenExpiresIn": 86400,
  "brokerUrl": "mqtt://broker.example:1883"
}
```
The bridge persists `{bridgeId, tenantId, bridgeSecret, token, brokerUrl}` to
`data/credentials.json` and remembers `tokenExpiresIn` for proactive refresh
(§5.4). `bridgeSecret` is the long-lived rotation credential — returned **once**;
`token` is the short-lived MQTT password. Non-200 (400 missing/invalid, 404
bad/used code) → pairing failed.

### 2.2 `POST /api/bridge/token` — rotate the MQTT token

Request:
```json
{"bridgeId": "…", "bridgeSecret": "…"}
```
Response (200): `{"token": "<JWT>", "tokenExpiresIn": 86400}`. A 401 means the
secret is bad (or was revoked by a `credentialVersion` bump). Source:
`app/auth.py` `refresh_token`.

### 2.3 `POST /api/bridge/ice-servers` — fetch WebRTC ICE servers

Same auth as token refresh (`{bridgeId, bridgeSecret}`). Response:
`{"iceServers": [ … ]}` (STUN + short-lived TURN, for go2rtc). The bridge fetches
these via `app/auth.py` `fetch_ice_servers` for its camera path (§11). Source:
`app/api/bridge/ice-servers/route.ts` + `lib/bridge/turn.ts`.

### 2.4 The MQTT token (JWT)

The token is an **HS256 JWT** signed by the app with `MQTT_JWT_SECRET`
(`lib/bridge/jwt.ts`). Claims:

| Claim | Meaning |
|-------|---------|
| `sub` | `bridgeId` — must equal the connecting MQTT username. |
| `tid` | `tenantId`. |
| `ver` | `credentialVersion` at issue time — checked against the DB on every connect. |
| `exp` | Expiry (`TOKEN_TTL_SECONDS` = 24h after issue). |

**Revocation** = the app bumps `Bridge.credentialVersion`; every previously-issued
token then fails the `ver` check at the broker and every later refresh mints
tokens with the new version.

---

## 3. Broker authentication & ACL

The broker delegates auth to the app's `/api/mqtt/{user,superuser,acl}` routes
(mosquitto-go-auth HTTP backend). Logic lives in `lib/bridge/mqtt-auth.ts`.

- **App subscriber:** static username/password (env). It is a **superuser** — the
  broker skips ACL, so it reads/writes every tenant's topics.
- **Bridge:** `username = bridgeId`, `password = <JWT>`. On connect the app
  verifies the JWT signature, that `sub == username`, that the bridge exists and
  is paired, that `tid` matches the bridge's tenant, and that `ver` matches the
  current `credentialVersion`. Any mismatch → connection refused (the broker
  returns a "Not authorized" CONNACK).
- **ACL:** a bridge may only publish/subscribe within its own
  `ga/<tenantId>/bridge/<bridgeId>/` subtree (`isTopicWithinBridge`). A
  `startsWith` on the fully-qualified prefix rejects cross-tenant, cross-bridge,
  and over-broad wildcard topics alike.

A "Not authorized" CONNACK triggers the bridge's reactive token refresh
(`app/mqtt_transport.py` `_handle_auth_failure`), which is the fallback for the
proactive refresh in §5.4.

---

## 4. Topic scheme

Every topic is tenant- and bridge-scoped under a
`ga/<tenantId>/bridge/<bridgeId>/` prefix — the authorization boundary (§3).
Canonical helpers: `lib/bridge/topics.ts` (app) and `app/mqtt_transport.py`
`_topic` (bridge).

| Topic suffix              | Direction     | Retained | Payload | Status |
|---------------------------|---------------|----------|---------|--------|
| `manifest`                | bridge → app  | yes      | §6      | live |
| `state`                   | bridge → app  | yes      | §7      | live |
| `telemetry`               | bridge → app  | no       | §8      | live |
| `cmd/<cmdId>`             | app → bridge  | no       | §9      | live |
| `cmd/<cmdId>/ack`         | bridge → app  | no       | §9      | live |
| `automations`             | app → bridge  | yes      | §10     | live |
| `automations/status`      | bridge → app  | yes      | §10     | live |
| `notify`                  | bridge → app  | no       | §10     | live |
| `webrtc/offer`            | app → bridge  | no       | §11     | live |
| `webrtc/answer`           | bridge → app  | no       | §11     | live |

The app subscribes to per-channel wildcards across all tenants, e.g.
`ga/+/bridge/+/manifest`, `ga/+/bridge/+/cmd/+/ack` (`inboundSubscription`,
`commandAckSubscription`, `webrtcAnswerSubscription`).

---

## 5. Connection lifecycle

Source: `app/mqtt_transport.py`.

### 5.1 Connect

The bridge owns reconnection via a **maintainer task** (`_maintainer_loop`) that
connects once credentials are present and rebuilds a fresh client after any drop
(paho's own auto-reconnect is disabled so a dropped client can't run in parallel
with the maintainer's replacement). On connect it:

1. Sets the **Last-Will-and-Testament** on `…/state`:
   `{"online": false}` (retained, qos 1) — the broker publishes it if the bridge
   dies without a clean disconnect.
2. Connects with `username = bridgeId`, `password = token`.
3. On a successful CONNACK: subscribes to `cmd/+` and `automations`, then
   publishes the manifest + online state (§6, §7).

### 5.2 Retained state & reconnect

`…/manifest`, `…/state`, and `…/automations` are **retained**, so a reconnecting
party (app or bridge) immediately receives the last-known values from the broker
without waiting for a fresh publish. Telemetry and command/ack messages are not
retained.

On a clean `stop()` the bridge publishes `{"online": false}` retained to
`…/state` and disconnects.

### 5.3 Offline queue

Telemetry produced while disconnected is buffered by `app/queue_manager.py`
(SQLite-backed, survives restarts). On reconnect the bridge replays the queue —
so telemetry ingest on the app **must be idempotent** and tolerant of
out-of-order timestamps (the app dedupes on `(entity, loggedAt)`, see
`lib/bridge/ingest.ts`).

### 5.4 Token refresh

The 24h MQTT token is refreshed **proactively** at ~90% of its TTL by a timer in
`app/auth.py` (`_proactive_refresh_loop`), so rotation is seamless and the bridge
never waits for the broker to reject an expired token. `tokenExpiresIn` from the
pair/refresh responses drives the schedule; after each refresh the timer
reschedules off the fresh TTL. Concurrent refreshes (proactive timer + reactive
CONNACK path) are coalesced into a single round-trip.

The **reactive** path remains as a fallback: a "Not authorized" CONNACK triggers
one refresh, then the maintainer reconnects with the new token.

---

## 6. Manifest (`…/manifest`, retained)

The bridge's announcement of which devices/entities it exposes. Published
retained; republished (with a bumped `manifestVersion`) on startup and on any
registry change. Sources: `app/registry.py` `serialize_manifest`;
app-side parser/mapper `lib/bridge/manifest.ts`.

### 6.1 Schema

Top-level:

| Field             | Type   | Notes |
|-------------------|--------|-------|
| `manifestVersion` | int    | Monotonically increasing per-bridge; persisted in `config_store`. |
| `generatedAt`     | string | ISO-8601 UTC (`Z` suffix). Informational. |
| `devices`         | array  | One entry per registered device, ASCII-sorted by `entityId`. |

Each device entry:

| Field             | Type     | In hash? | Notes |
|-------------------|----------|----------|-------|
| `entityId`        | string   | yes      | `<domain>.<name>` — stable id and telemetry join key (§13). |
| `domain`          | string   | yes      | Integration/transport domain (`gpio`/`mqtt`/`http`/`esphome`…), lowercase. |
| `name`            | string   | yes      | Device's local name within its domain. |
| `deviceType`      | string   | yes      | Free-form type (`pump`, `fan`, `light`, `temperature`, …). |
| `category`        | string   | yes      | **Uppercase** `SENSOR` or `ACTUATOR`. |
| `integrationName` | string   | yes      | Integration class name (`GPIOIntegration`). |
| `capabilities`    | string[] | yes (sorted) | Action verbs the device supports (`["on","off"]`). |
| `metadata`        | object   | **no**   | Free-form; defaults to `{}`. Excluded from the hash. |
| `entityDomain`    | string   | **no**   | HA entity domain the app stores: `sensor`/`switch`/`number`/`light`/`camera`. |
| `writable`        | bool     | **no**   | True for actuators — whether the app may command it. |
| `unit`            | string?  | **no**   | Unit of measurement, or null. |

The bridge emits the HA `entityDomain` explicitly (rather than the app inferring
it): `SENSOR → sensor`; `ACTUATOR` with `deviceType == "light" → light`;
`ACTUATOR` with a settable capability (`speed`/`level`/`temperature`/`set`) →
`number`; any other `ACTUATOR → switch` (`app/registry.py` `_ha_entity_domain`).
The `camera` entity domain is used by the bridge's camera integration
(`app/integrations/camera/`), whose WebRTC path is described in §11. The
app's domain enum is closed — an `entityDomain` the app doesn't model is
rejected (`lib/bridge/manifest.ts` `mapManifestDevice`).

### 6.2 Manifest hash algorithm

Both sides compute a SHA-256 over a deterministic serialization of the device set
to detect drift. Bridge: `app/registry.py` `compute_manifest_hash`. App:
`lib/bridge/manifest.ts` `computeManifestHash`. **The two must stay byte-exact**
or the app will treat every manifest as changed.

```python
items = []
for entity_id in sorted(devices.keys()):
    d = devices[entity_id]
    payload = {
        "entityId": entity_id,
        "domain": d.domain,
        "name": d.name,
        "deviceType": d.device_type,
        "category": d.category.upper(),   # "SENSOR" | "ACTUATOR"
        "integrationName": d.integration_name,
        "capabilities": sorted(d.capabilities),
    }
    items.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
hash = sha256("\n".join(items).encode("utf-8")).hexdigest()
```

Byte-exact requirements: ASCII-sorted `entityId` iteration; exactly the seven
keys above; compact separators `(",", ":")` (no spaces); `sort_keys=True`;
uppercase `category`; **sorted** `capabilities`; `metadata`/`entityDomain`/
`writable`/`unit` **excluded**; lines joined with `\n` (no trailing newline);
UTF-8 → SHA-256 → lowercase hex.

#### 6.2.1 Verified fixture

Two-device registry:

```python
[
  {"name": "pump1", "domain": "gpio", "device_type": "pump",
   "category": "ACTUATOR", "integration_name": "GPIOIntegration",
   "capabilities": ["on", "off"]},
  {"name": "temp1", "domain": "mqtt", "device_type": "temperature",
   "category": "SENSOR",   "integration_name": "MQTTIntegration",
   "capabilities": []},
]
```

Hash input (two lines joined with one `\n`):

```
{"capabilities":["off","on"],"category":"ACTUATOR","deviceType":"pump","domain":"gpio","entityId":"gpio.pump1","integrationName":"GPIOIntegration","name":"pump1"}
{"capabilities":[],"category":"SENSOR","deviceType":"temperature","domain":"mqtt","entityId":"mqtt.temp1","integrationName":"MQTTIntegration","name":"temp1"}
```

Expected SHA-256:

```
f5b1954d657d7247d578bd15ff4e4bca827986bd88bc1c6a086886ac0ed158df
```

### 6.3 Example manifest

```json
{
  "manifestVersion": 12,
  "generatedAt": "2026-07-15T14:22:00.123456Z",
  "devices": [
    {
      "entityId": "gpio.water_pump",
      "domain": "gpio",
      "name": "water_pump",
      "deviceType": "pump",
      "category": "ACTUATOR",
      "integrationName": "GPIOIntegration",
      "capabilities": ["on", "off"],
      "metadata": {"pin": 17},
      "entityDomain": "switch",
      "writable": true,
      "unit": null
    },
    {
      "entityId": "mqtt.tent_temp",
      "domain": "mqtt",
      "name": "tent_temp",
      "deviceType": "temperature",
      "category": "SENSOR",
      "integrationName": "MQTTIntegration",
      "capabilities": [],
      "metadata": {"topic": "tent/sensors/temp"},
      "entityDomain": "sensor",
      "writable": false,
      "unit": "°C"
    }
  ]
}
```

### 6.4 Versioning & soft-removal

- `manifestVersion` is monotonic per-bridge, bumped by 1 on each publish
  (`app/mqtt_transport.py` `send_manifest`, serialized under an async lock so
  concurrent registry changes coalesce into one publish).
- The manifest is **retained**, so the app always sees the latest on reconnect.
- **Soft removal:** the app should mark any `(bridgeId, entityId)` not present in
  the latest manifest as `removed` (soft delete), and reactivate the same row if
  the `entityId` reappears — preserving history and assignments across transient
  hardware drop-outs.

---

## 7. State (`…/state`, retained)

Bridge liveness + manifest-freshness hints. Parser: `lib/bridge/telemetry.ts`
`parseState`.

| Field             | Type    | Notes |
|-------------------|---------|-------|
| `online`          | bool    | `false` only when the bridge said so (clean stop) or via LWT. Any state message without it implies online. |
| `manifestHash`    | string? | SHA-256 of the current device set (§6.2), or null. |
| `manifestVersion` | int?    | The current manifest version, or null. |

Published retained on connect as `{"online": true, "manifestHash": …,
"manifestVersion": …}` and set as the LWT `{"online": false}` so a hard death
flips the bridge offline without a graceful disconnect.

---

## 8. Telemetry (`…/telemetry`)

A batch of timestamped samples, bridge → app, qos 1, **not retained**. Source:
`app/mqtt_transport.py` `send_data`; parser `lib/bridge/telemetry.ts`.

```json
{
  "samples": [
    {"entityId": "mqtt.tent_temp", "value": "23.6", "ts": "2026-07-15T14:22:31.412Z"},
    {"entityId": "gpio.water_pump", "value": "on",  "ts": "2026-07-15T14:22:31.500Z"}
  ]
}
```

| Field      | Type                    | Notes |
|------------|-------------------------|-------|
| `entityId` | string                  | Must match a manifest `entityId` to be joinable (§13). |
| `value`    | string \| number \| bool | Raw value; the app keeps the string form and parses a numeric form when possible. |
| `ts`       | string                  | ISO-8601 bridge event time. |

Ingest is idempotent and order-tolerant (§5.3): the bridge replays its offline
queue on reconnect and may resend points.

**Telemetry contract (bridge-internal).** Every integration's `receive_data`
yields samples built by `Integration.telemetry_sample` — an explicit dotted
`entity_id` equal to what `register_capabilities` registered, plus a top-level
`value`. The transport publishes those verbatim, so a sample always joins its
manifest entity app-side. For third-party external integrations that predate
the contract, the transport falls back to deriving the id from the integration
name + a device-name key via the shared `app/entity_id.py` `derive_domain` —
the **same** derivation the manifest uses — and to extracting a value nested
under `data`. Samples with no derivable entity id or no usable value are
dropped at the bridge and counted (visible on the bridge dashboard's
telemetry panel), never published as junk ids.

---

## 9. Commands (`…/cmd/<id>` + `…/cmd/<id>/ack`)

The app→bridge write path (dashboard widgets). The app publishes a command to
`cmd/<id>` and awaits the bridge's echo on `cmd/<id>/ack`. Sources:
`lib/bridge/commands.ts` (app); `app/main.py` `_process_command` +
`app/mqtt_transport.py` (bridge).

### 9.1 Command (`cmd/<id>`, app → bridge)

```json
{
  "id": "cmd_8f2c…",
  "targetType": "actuator",
  "targetId": "gpio.water_pump",
  "action": "on",
  "payload": {"value": 250}
}
```

| Field        | Type   | Notes |
|--------------|--------|-------|
| `id`         | string | App-generated; echoed in the ack. |
| `targetType` | string | Always `"actuator"` — only writable entities are commandable. |
| `targetId`   | string | The full `<domain>.<name>` **entityId** of the target (the manifest join key, §13). |
| `action`     | string | Bridge vocabulary: `"on"` / `"off"` / `"set"`. The app has already translated any HA service to this (there is no service translation on the command path — that seam only exists in the bridge's automations executor). |
| `payload`    | object | Free-form; a `set` carries `{"value": …}`. Passed verbatim to `integration.execute_command`. |

The bridge resolves a dotted `targetId` through the registry
(`registry.get_device(entityId)` → owning integration + local device name),
exactly like the automations executor resolves rule targets — unambiguous
across integrations. An unknown entityId is acked `success:false` with
`"Unknown entity: …"`.

> **Backward compatibility.** A bare device name (no dot) is still accepted and
> resolved through the legacy name-indexed lookup
> (`registry.get_actuator_integration`), so bridges behind on updates keep
> working with older app versions. Bare names are ambiguous if two domains
> register the same actuator name — new senders must use the full entityId.

### 9.2 Ack (`cmd/<id>/ack`, bridge → app)

```json
{"id": "cmd_8f2c…", "success": true, "message": "", "ts": 1752589351412}
```

Published exactly once per command, after `integration.execute_command` runs.
`ts` is ms-epoch. On any failure the bridge still acks with `success:false` and a
diagnostic message. The app correlates by `id` and materialises an optimistic
value on success so the dashboard reflects the new state before the next
telemetry cycle (`lib/bridge/commands.ts` `optimisticValueFor`).

The app subscribes to `cmd/+/ack`; the bridge ignores its own ack echoes on the
`cmd/+` subscription (`app/mqtt_transport.py` `_on_message`).

---

## 10. Automations (`…/automations` + `…/automations/status`)

The app produces the rule set (`lib/automations/publish.ts`); the bridge consumes
it, validates + evaluates it, and echoes status. Bridge implementation:
`app/automations/` (engine, executor, event bus, state store, templates,
manager) and `app/mqtt_transport.py` (subscription + status publish).

### 10.1 Rule set (`…/automations`, app → bridge, retained)

A full **snapshot** of every automation for the bridge (including disabled ones,
carried with `enabled:false` so the bridge can tell "disabled" from "deleted") —
never a delta. Republished in full on every mutation. Source:
`lib/automations/publish.ts`; rule grammar in `lib/automations/schema.ts`.

```json
{
  "automations": [
    {
      "id": "auto_1",
      "name": "Vent on high temp",
      "enabled": true,
      "triggers": [ … ],
      "conditions": [ … ],
      "actions": [ … ]
    }
  ],
  "version": 7
}
```

- `version` is a monotonic per-bridge integer bumped on every publish. The bridge
  applies a rule set **only if strictly newer** than the last applied version, so
  a retained redelivery on reconnect is ignored.
- **Clearing** the last automation publishes `{"automations": [], "version": N}`
  **retained** (never an empty payload — an empty retained message would *delete*
  the retained message, and a bridge offline at clear time would then never learn
  the set was emptied). The retained message is never deleted, so the broker
  always replays the latest desired set to a reconnecting bridge.

The full trigger/condition/action vocabulary is in `lib/automations/schema.ts`
(triggers `state`/`numeric_state`/`time`/`time_pattern`/`event`; recursive
`and`/`or`/`not` + `state`/`numeric_state`/`time` conditions; actions `call`/
`delay`/`wait_for_state`/`set_variable`/`fire_event`/`notification`; `{{ }}`
templating). The bridge evaluator (`app/automations/`) implements that full
vocabulary. The `notification` action publishes back to the app on `…/notify`
(§10.3).

### 10.2 Status echo (`…/automations/status`, bridge → app, retained)

After receiving a rule set the bridge validates + applies it and publishes the
result retained. Source/parser: `lib/automations/status.ts`.

```json
{
  "ok": true,
  "count": 3,
  "validatedHash": "…sha256 hex…",
  "validatedAt": "2026-07-15T14:25:00Z",
  "errors": [{"automationId": "auto_2", "message": "unknown entity light.foo"}]
}
```

| Field           | Type    | Notes |
|-----------------|---------|-------|
| `ok`            | bool    | Whether the whole set validated/applied. |
| `count`         | int     | Number of automations applied. |
| `validatedHash` | string  | **SHA-256 of the exact bytes the bridge received** on `…/automations`. |
| `validatedAt`   | string? | Bridge-reported time (the app records its own receipt time to avoid Pi clock skew). |
| `errors`        | array   | `{automationId?, message}` per rejected rule. |

**Cross-language hash parity is load-bearing.** The app records the SHA-256 of
the exact bytes it published (`lib/automations/publish.ts` `hashPayload`); the
bridge must echo the SHA-256 of the bytes it received. A set reads as **synced**
only when `validatedHash == publishedHash` **and** `ok` is true; matching hash
with `ok:false` → **error**; mismatched hash → **pending** (`deriveSyncState`).
Count/timestamp alone are insufficient — a same-count edit must read as pending
until the bridge confirms the new bytes.

### 10.3 Notification intent (`…/notify`, bridge → app)

A `notification` action publishes a notification intent to `…/notify` (qos 1,
**not retained**) each time a rule carrying it fires. The app fans it out as Web
Push to the tenant's subscriptions — the bridge never talks to push endpoints
itself. Source: `app/automations/engine.py` `_action_notification` +
`app/mqtt_transport.py` `publish_notification`.

The action carries a `title` and a `message` (both required, non-empty strings),
each of which may contain `{{ … }}` templates the bridge renders at fire time
against the same context as every other templated string (`variables` /
`trigger` / `states`):

```json
{"type": "notification", "title": "Tent is {{ states['sensor.temp'] }}°C", "message": "High temperature — check ventilation"}
```

The published payload:

```json
{
  "automationId": "auto_1",
  "title": "Tent is 31°C",
  "message": "High temperature — check ventilation",
  "firedAt": "2026-07-16T12:00:00.123456+00:00"
}
```

| Field          | Type   | Notes |
|----------------|--------|-------|
| `automationId` | string | The `id` of the rule that fired. |
| `title`        | string | Rendered notification title. |
| `message`      | string | Rendered notification body. |
| `firedAt`      | string | ISO-8601 UTC time the action fired (same format as the status echo's `validatedAt`). |

The bridge does not retry or await delivery — it publishes the intent and moves
on to the next action; the app owns fan-out and any per-subscription retry.

---

## 11. WebRTC camera signalling (`…/webrtc/offer` + `…/webrtc/answer`)

App-side signalling is in `lib/bridge/webrtc.ts` + `lib/bridge/turn.ts`; the
bridge camera integration (`app/integrations/camera/`) supervises go2rtc,
proxies the SDP, and publishes the answer via `app/mqtt_transport.py`
`send_webrtc_answer`.

The browser owns the offer, so this is request/response (not an unsolicited
push). The app publishes an offer and awaits the answer, correlating by a
`sessionId` (these topics carry no id segment, unlike `cmd/<id>/ack`).

### 11.1 Offer (`…/webrtc/offer`, app → bridge)

```json
{"sessionId": "sess_…", "streamId": "camera.tent1", "sdp": "<SDP offer>"}
```
`streamId` is the camera entity's HA ref (`camera.<name>`), resolved server-side
from the entity id and used as the go2rtc `?src=`. The bridge proxies the SDP to
its local go2rtc `/api/webrtc` and echoes the answer.

### 11.2 Answer (`…/webrtc/answer`, bridge → app)

`{"sessionId": "sess_…", "ok": true, "sdp": "<SDP answer>"}` or, on failure,
`{"sessionId": "sess_…", "ok": false, "error": "…"}`.

### 11.3 Low-framerate variant

The browser can request a reduced-framerate stream when its path is TURN-relayed;
the app appends the `_lofps` suffix to the stream id server-side
(`lib/bridge/webrtc.ts` `LOW_FRAMERATE_STREAM_SUFFIX`), so the bridge must expose
a `camera.<name>_lofps` variant per camera. ICE servers for go2rtc are fetched
via §2.3.

---

## 12. Device classification (tags)

Devices are classified **app-side with tags** — free-form, tenant-scoped
labels attached to entities/devices in the app's database (`Tag`,
`entity_tags`, `device_tags`). The bridge plays no part in classification:
nothing about tags crosses the wire, the bridge stores no assignments, and
command routing never consults them. The bridge's job ends at announcing what
exists (manifest: `deviceType`, `entityDomain`, `writable`, `unit`) and
carrying telemetry/commands for it.

On the app side, onboarding offers a curated tag set
(`lib/onboarding/templates.ts` `CURATED_TAGS`: `temperature`, `humidity`,
`light`, `fan`, `soil-moisture`, `ec`, `ph`, `water-temp`) and starter
dashboard templates bind widgets by those tags. Beyond the curated set, tags
are unconstrained — no cardinality rules, no compatibility matrix.

> **History.** An earlier revision of this section specified a shared
> `GrowRole` taxonomy (WATER_PUMP, EXHAUST_FAN, …) with per-space cardinality
> rules, and the bridge briefly stored role assignments for dashboard labels.
> That design was dropped (2026-07-17, "drop roles, keep tags") before the app
> ever implemented it: the app's shipped classification is tags, and the
> bridge's role-assignment storage was removed as dead code.

---

## 13. Stable identity rules

### 13.1 `entityId = <domain>.<name>`

Every device's `entityId` is the dotted concatenation of its `domain` and `name`.
The **same** value appears in the manifest and in telemetry — it is the join key,
and it must never drift between the two. Source of truth: `app/entity_id.py`
`derive_entity_id`, used by both the manifest side (`app/registry.py`) and the
telemetry side (`app/mqtt_transport.py`).

- `domain` is lowercase.
- `name` is whatever the integration registered; by convention it has no dots,
  but only the *first* dot matters for parsing.
- The pair `(bridgeId, entityId)` is the stable primary key for an app-side
  device row.

### 13.2 Domain derivation

When an integration registers without an explicit `domain`, it is derived from
the integration class name (`app/entity_id.py` `derive_domain`): strip a trailing
`Integration` suffix (case-sensitive, exactly that literal), lowercase the rest.

| Integration class   | Domain |
|---------------------|--------|
| `GPIOIntegration`   | `gpio` |
| `MQTTIntegration`   | `mqtt` |
| `HTTPIntegration`   | `http` |
| `SerialIntegration` | `serial` |
| `ESPHomeIntegration`| `esphome` |
| `DHTSensor` (no suffix) | `dhtsensor` |

### 13.3 Stability across restarts

The registry is rebuilt from `config.yaml` on every start; it is not persisted.
An `entityId` is stable iff the integration class name (or explicit `domain`
override) and the device's `name` are unchanged. The app persists device rows; an
`entityId` that disappears during a restart and reappears in the next manifest is
the **same** logical device (§6.4).

---

## 14. Liveness model

Liveness is derived from the retained `…/state` message and telemetry, not from a
per-device heartbeat:

- **Bridge online/offline:** the retained `…/state` `online` flag. The LWT flips
  it to `false` on a hard death; a clean stop publishes `{"online": false}`.
  Because it is retained, the app sees the current liveness immediately on
  subscribe.
- **Per-device freshness:** the app updates a device's `lastSeen` as a side
  effect of telemetry whose `entityId` matches a known device. A device present
  in the manifest but never producing telemetry is "registered but never seen".
- **Manifest freshness:** the `…/state` `manifestHash`/`manifestVersion` let the
  app detect whether it holds the current manifest without diffing every device.

A recommended per-device staleness threshold is ~3 minutes since `lastSeen`,
derived app-side; the bridge does not consume it.

---

## 15. Glossary

- **`bridgeId`** — stable id issued by the app at pairing; the bridge's MQTT
  username and the `sub` claim of its token.
- **`tenantId`** — the tenant a bridge belongs to; part of every topic and the
  token's `tid` claim.
- **`entityId`** — `<domain>.<name>`; the stable device identifier and telemetry
  join key (§13).
- **`bridgeSecret`** — long-lived rotation credential returned once at pairing;
  used to mint MQTT tokens and fetch ICE servers.
- **MQTT token** — short-lived (24h) HS256 JWT; the bridge's MQTT password.
- **`credentialVersion`** — per-bridge revocation counter embedded in the token
  (`ver`); a bump invalidates every prior token.
- **`manifestVersion`** — monotonic per-bridge manifest counter.
- **`manifestHash`** — SHA-256 of the device set (§6.2); drift signal in `…/state`.
- **automations `version`** — monotonic per-bridge rule-set counter; the bridge
  applies only strictly-newer sets.
- **`validatedHash`** — SHA-256 of the exact automations bytes the bridge
  received; matched against the app's published hash to derive sync state.
- **LWT** — MQTT Last-Will-and-Testament; the retained `{"online": false}` the
  broker publishes to `…/state` if the bridge dies uncleanly.
- **Tags** — app-side device classification labels (§12); app-owned, never on the wire.

---

## 16. Open questions / pending contract notes

1. **Command `targetId` (bare name vs entityId) — RESOLVED.** Commands now
   carry the full `entityId` (§9.1); the bridge resolves it through the
   registry. Bare names remain accepted for backward compatibility.
2. **Broker-secret User-Agent gate.** `lib/bridge/mqtt-auth.ts` supports an
   optional shared-secret `User-Agent` (`MQTT_HTTP_BROKER_SECRET`) as
   defence-in-depth on the `/api/mqtt/*` routes; the primary control is keeping
   those routes off the public ingress.
3. **Soft-removal.** The app owns soft-removal of devices absent from the
   latest manifest (§6.4); tags survive soft-removal, so a device that
   reappears keeps its classification.
