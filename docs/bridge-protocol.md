# GrowAssistant Bridge Protocol Specification

This document specifies the over-the-wire contract between a **GrowAssistant
Bridge** instance (this repository) and a **GrowAssistant API** server.
A backend engineer should be able to implement an API server that fully
interoperates with the bridge by reading this document alone, without
opening the bridge source tree.

The contract described here is the **post-Phase-5** contract. All legacy
`pump_num` / `pumpNum` / `pumpNumber` fields have been removed from the
bridge's outbound payloads and inbound action handling. Devices are
identified exclusively by `entity_id` (`<domain>.<name>`).

Where the document cites bridge source it uses the form
`app/api_client.py:402`. Cited paths are relative to the bridge repo.

---

## Table of contents

- [1. Overview](#1-overview)
- [2. Connection lifecycle](#2-connection-lifecycle)
- [3. Outbound HTTP endpoints (bridge → API)](#3-outbound-http-endpoints-bridge--api)
- [4. Inbound SSE event stream (API → bridge)](#4-inbound-sse-event-stream-api--bridge)
- [5. Manifest payload reference](#5-manifest-payload-reference)
- [6. Role taxonomy (`GrowRole`)](#6-role-taxonomy-growrole)
- [7. Stable identity rules](#7-stable-identity-rules)
- [8. Liveness model](#8-liveness-model)
- [9. Multi-pod / failure-mode notes](#9-multi-pod--failure-mode-notes)
- [10. Glossary](#10-glossary)
- [11. Open questions / future contract notes](#11-open-questions--future-contract-notes)

---

## 1. Overview

The bridge is a NAT-bound agent. It runs inside the operator's grow room,
behind their home router, and never accepts inbound TCP connections.
The API server is reachable on a public hostname. All transport is bridge-
initiated:

- **REST POSTs** push the device manifest, telemetry, and command results
  from bridge to API.
- **A single long-lived SSE GET** (`text/event-stream`) is held open by the
  bridge. The API uses it to deliver config snapshots, command requests,
  and heartbeats.

The bridge is the **source of truth for which devices physically exist**.
The API is the **source of truth for desired state** (light schedules,
climate setpoints, role assignments, etc.). Cardinality conflicts,
role-compatibility validation, and persistence of the inventory across
bridge restarts are all the API's responsibility.

```
                         ┌────────────────────┐
                         │  GrowAssistant API │
                         │  (public host)     │
                         └─────────┬──────────┘
                                   │
                  POST /bridge/{id}/manifest    ← device inventory + hash
                  POST /bridge/{id}/data        ← telemetry batch
                  POST /bridge/{id}/actions/{aid}/result
                  GET  /bridge/{id}/stream      ← SSE (held open by bridge)
                                   │
   ┌───────────────────────────────┴──────────────────────────────┐
   │  Bridge                                                       │
   │  ─ DeviceRegistry (entity_id = domain.name)                   │
   │  ─ ConfigStore (sqlite: configVersion, manifestVersion, hash) │
   │  ─ Integrations (GPIO, MQTT, HTTP, plugins)                   │
   │  ─ SSE consumer:                                              │
   │     event:connected   → log handshake                         │
   │     event:config      → save snapshot, fan out settings       │
   │     event:heartbeat   → drift detection (config + manifest)   │
   │     event:action      → enqueue command for execution         │
   └───────────────────────────────────────────────────────────────┘
```

### Identity primitives

There are exactly two stable identifiers in the protocol:

| Name        | Format                  | Lifetime                                          | Source            |
|-------------|-------------------------|---------------------------------------------------|-------------------|
| `bridge_id` | UUID (string)           | Stable for the bridge's hardware install          | Issued by API at pairing time, persisted in `data/credentials.json` (`auth.py:84`) |
| `entity_id` | `<domain>.<name>`       | Stable across bridge restarts and registry rebuilds | Constructed by `DeviceRegistry.register_device` (`registry.py:106`) |

Everything else (`configVersion`, `manifestVersion`, action `id`) is
either monotonic per-bridge state or per-request scratch.

---

## 2. Connection lifecycle

### 2.1 Pairing

The bridge has no preconfigured credentials. On first start (or after
`request_new_code`) it registers itself, receives a `bridge_id` and a
five-character pairing code, then polls until the operator binds the
bridge to a space in the consumer app.

Endpoints involved (all caller: bridge):

1. `POST /bridge` — register a new bridge instance.
   Request body:
   ```json
   {"customId": "raspberrypi-3a7c1b9e"}
   ```
   `customId` is `<hostname>-<8-hex-uuid>` (`auth.py:182`). It is
   informational only — the API generates and returns the canonical
   `bridge_id`.

   Response body (200 OK):
   ```json
   {"id": "<bridge_id-uuid>", "code": "AB12C"}
   ```
   The bridge persists `id` to `data/credentials.json` and displays
   `code` to the operator. The operator types `code` into the
   GrowAssistant app to bind this bridge to their space.
   See `auth.py:131-180`.

2. `GET /bridge/{bridge_id}` — poll for connection state.
   Returns:
   - **204 No Content** — bridge is registered but no space has been
     created/linked yet. Bridge interprets this as "connected, not
     ready" and continues polling on `AUTH_POLL_INTERVAL`. (`auth.py:266`)
   - **200 OK** — space exists, bridge is fully provisioned. The
     response body is the full `BridgeSpaceResp` (see §4.2 `config`).
     The bridge marks itself `ready` and stops polling. (`auth.py:273`)
   - Any other status — treated as transient and retried.

   This endpoint is **also** used as an SSE-fallback fetch any time the
   bridge needs the current config snapshot synchronously
   (`fetch_full_config`, `api_client.py:623`).

### 2.2 Authentication

The bridge currently authenticates by including its `bridge_id` in the
URL path (`/bridge/{bridge_id}/...`) and as the `X-Client-ID` header.
There is **no HMAC or bearer token** in the current bridge implementation
on the bulk-data and SSE paths. Headers are produced by
`build_auth_headers(client_id=...)` in `app/utils/http_utils.py:35-60`.

The exact header set sent on every authenticated request:

| Header           | Value                                  |
|------------------|----------------------------------------|
| `Content-Type`   | `application/json`                     |
| `Accept`         | `application/json` (or `text/event-stream` for SSE) |
| `X-Client-ID`    | `<bridge_id>`                          |
| `Authorization`  | `Bearer <token>` *(only when a token is present in stored credentials; `auth.py:128`)* |

See `api_client.py:139-142`. Note: the `Authorization` header is wired
through `build_auth_headers` for forward compatibility but the bridge
never receives a token in the current pairing flow. **The new API
should treat `X-Client-ID` as the only proven identity claim today and
should add HMAC signing as a follow-up** — see §11.

### 2.3 Startup sequence

`Application.start()` (`main.py:85-128`) runs in this order:

1. `auth_manager.start()` — load credentials, open auth HTTP client.
2. Web UI thread starts (Flask, `web/app.py`).
3. `_handle_authentication()` — register if needed; poll for connection;
   poll for space creation. Bridge sleeps here until paired.
4. `queue_manager.start()` and `config_store.start()` — open SQLite.
5. `api_client.start()` — open the main HTTP client; subscribe to
   `registry.add_change_callback` for manifest re-push.
6. `_load_config_from_store()` — apply any locally-cached config from a
   previous run so the bridge has working setpoints before SSE connects.
7. `_load_integrations()` — discover plugin classes, instantiate enabled
   ones, call `connect()`, then `register_capabilities(registry)`. This
   populates the device registry.
8. **First manifest push** — `api_client.send_manifest()` is awaited
   inline. If auth completed and the registry has any devices, the API
   gets v1 of the manifest before SSE opens.
9. `start_sse_listener()` — begin the SSE consumer loop.
10. Internal asyncio tasks (`_data_collection_task`,
    `_data_transmission_task`, `_command_execution_task`) are spawned.

If step 3 has not completed (operator hasn't entered the pairing code),
step 8 is skipped silently. The registry-change callback re-fires the
manifest push the moment auth succeeds.

### 2.4 Reconnection / retry / backoff

REST calls (`/manifest`, `/data`, action result) use `tenacity`
with exponential backoff. Defaults from `app/constants.py`:

| Setting              | Default | Config key (`config.yaml`)        |
|----------------------|---------|-----------------------------------|
| Max attempts         | 5       | `api.retry_max_attempts`          |
| Min backoff (s)      | 1       | `api.retry_min_backoff`           |
| Max backoff (s)      | 60      | `api.retry_max_backoff`           |
| HTTP timeout (s)     | (TIMEOUT default) | `api.timeout`           |

Retried exception classes: `httpx.HTTPError`, `httpx.ConnectError`,
`asyncio.TimeoutError`. 4xx responses raised as `HTTPStatusError` are
**not** retried — they are treated as a hard reject and the batch is
dropped (data) or the manifest version is left unchanged
(manifest).

The SSE consumer (`api_client.py:674-694`) uses its own backoff:

| Setting                  | Value | Defined in              |
|--------------------------|-------|-------------------------|
| Initial reconnect delay  | 1 s   | `SSE_RECONNECT_MIN`     |
| Max reconnect delay      | 60 s  | `SSE_RECONNECT_MAX`     |
| Stream read timeout      | 90 s  | `SSE_STREAM_TIMEOUT`    |

The 90 s read timeout is intentionally longer than the 15 s heartbeat
cadence (see §4.2) so a missed heartbeat eventually trips the timeout
and forces a reconnect. Backoff doubles up to `SSE_RECONNECT_MAX` until
a connection succeeds, then resets.

On reconnect, the bridge sends the current local `configVersion` as a
query parameter (see §4.1). The API uses this to decide whether to
re-send a `config` event immediately.

### 2.5 Connection state

The bridge has three operationally-meaningful states, derived from
`auth_manager`:

| State            | Test                                          | Meaning                                         |
|------------------|-----------------------------------------------|-------------------------------------------------|
| `not_authenticated` | `is_authenticated()` is False              | No `bridge_id` issued yet.                      |
| `connected`      | `is_authenticated()` and not `is_ready_for_data()` | Bridge has a `bridge_id`; no space linked yet (API returns 204 to `GET /bridge/{id}`). |
| `ready`          | `is_ready_for_data()` returns True            | Space linked; manifest/data may flow.           |

REST writes (`/data`, `/actions/.../result`) are gated by `ready`.
The manifest push and SSE listener also gate on at least
`is_authenticated()`.

---

## 3. Outbound HTTP endpoints (bridge → API)

All paths are relative to `api.url` from `config.yaml` (default
`http://localhost:8080`). Trailing slashes are stripped on construction.
Headers are as listed in §2.2.

### 3.1 `POST /bridge` — register

See §2.1. The only endpoint the bridge calls before it has a `bridge_id`.

Request body:
```json
{"customId": "string (required)"}
```

Response body (200 OK):
```json
{"id": "string (uuid, required)", "code": "string (5 chars, required)"}
```

The bridge raises on any non-2xx; retried per §2.4.

### 3.2 `GET /bridge/{bridge_id}` — fetch full config / probe state

Used both during the pairing poll (§2.1) and as an SSE fallback
(`fetch_full_config`, `api_client.py:623-653`).

| Status | Bridge interpretation                                              |
|--------|--------------------------------------------------------------------|
| 200    | Body parsed as full `BridgeSpaceResp` (§4.2). Cached locally.       |
| 204    | "Connected but no space yet" — the bridge stays in `connected` state. |
| Other  | Logged; treated as transient by the pairing poll, fatal-for-this-fetch by the SSE fallback. |

### 3.3 `POST /bridge/{bridge_id}/manifest` — push device inventory

The full schema and example are in §5. Wire details:

| Item               | Value                                                              |
|--------------------|--------------------------------------------------------------------|
| Method             | `POST`                                                             |
| Path               | `/bridge/{bridge_id}/manifest`                                     |
| Headers            | `Content-Type: application/json`, `Accept: application/json`, `X-Client-ID: {bridge_id}` |
| Body               | See §5 — `{manifestVersion, generatedAt, devices: [...]}`          |
| Response (success) | 200 OK with optional body `{"acceptedVersion": <int>}` (`api_client.py:558-566`). Empty body is also accepted; the bridge falls back to the version it sent. |
| Response (reject)  | Any 4xx or 5xx is treated as a failure. The bridge does **not** advance its persisted `manifest_version`; the next change-driven push retries with the same `next_version = current + 1`. |
| Idempotency        | Re-pushing the same manifest content is safe — the API should de-dup by content hash (it produces the same hash; see §5.2). |
| Ordering           | The bridge serializes pushes via an `asyncio.Lock` (`api_client.py:523`); concurrent registry changes coalesce into one push. |

**When the bridge fires this:**

- Once on startup, after integrations finish loading and the registry
  is populated (`main.py:117-122`).
- Whenever `DeviceRegistry` fires a change callback — i.e. any
  `register_device` or removal (`registry.py:181-188`,
  `api_client.py:474-500`).
- Whenever an SSE `heartbeat` event reports a `manifestHash` that
  differs from the bridge's locally-stored one (`api_client.py:858-869`).

**Versioning semantics (this is the critical contract):**

- The bridge persists a monotonic `manifest_version` integer in
  `config_store` (`local_config` table, key `manifest_version`).
- Each push uses `next_version = stored_version + 1`
  (`api_client.py:527-529`).
- On 2xx response the bridge writes back **the API's
  `acceptedVersion`** (or `next_version` if the body is empty).
- The API SHOULD reject a push whose `manifestVersion` is **less than**
  the highest version it has accepted for this `bridge_id`. The
  recommended response is **409 Conflict** with body
  `{"acceptedVersion": <currentHighest>}`. The bridge currently treats
  any 4xx as a hard reject and does not advance. *(This 409-with-version
  contract is recommended; the current bridge does not yet bump its
  local counter on a soft reject — see §11.)*
- `acceptedVersion < manifestVersion` in a 200 response is allowed and
  means "I'm overriding your version downward"; the bridge will use
  whatever the API echoes.

### 3.4 `POST /bridge/{bridge_id}/data` — telemetry batch

Source: `api_client.send_data` (`api_client.py:381-470`).

| Item    | Value                                                                                |
|---------|--------------------------------------------------------------------------------------|
| Method  | `POST`                                                                               |
| Path    | `/bridge/{bridge_id}/data`                                                           |
| Headers | Standard auth headers (§2.2)                                                         |
| Body    | `{"dataLogs": [DataLogReq, ...]}` *(only `dataLogs` is sent on the post-Phase-5 path)* |
| Response (success) | 200 OK with **no body**. Config and action delivery happen exclusively over SSE. |

**`DataLogReq` schema** (`app/api_types.py:79-94`, `app/types.py:7-12`):

| Field      | Type    | Required | Notes                                                           |
|------------|---------|----------|-----------------------------------------------------------------|
| `logDate`  | string  | yes      | ISO-8601, UTC. Produced via `datetime.utcnow().isoformat()`.    |
| `logType`  | string  | yes      | Uppercase `LogType` enum value (see §3.4.1).                    |
| `value`    | string  | yes      | Always serialized as a string, even for numerics. Float/int are coerced via `str(value)`. |
| `deviceId` | string  | optional | The device's `entity_id` (`<domain>.<name>`). Drives the **liveness touch** (§8). When absent, the API SHOULD still record the value but MUST NOT touch any device's `lastSeen`. |

Ordering of entries within `dataLogs` is **not** significant. Duplicates
within a batch are not deduped by the bridge.

**Cadence:** The bridge runs `_data_transmission_task` every
`api.transmission_interval` seconds (default 60, `main.py:327`). It
pulls up to `api.batch_size` (default 100, `main.py:326`) points from
`queue_manager` and posts them in one request. On HTTP failure the
points are re-queued (`main.py:355-357`).

**Out-of-band problem reporting:** The bridge maintains a `_problems`
list (`api_client.py:194-198`) and an `_actions` ack list
(`api_client.py:201-202`), but the post-Phase-5 wire format only sends
`dataLogs`. Detected sensor faults (range/connection failures, see
`_detect_problems_from_data` at `api_client.py:317-377`) are currently
logged locally but no longer sent in the data batch payload. **The new
API can either ignore these fields or, if it wants problem visibility,
the bridge can re-introduce a `problems: [ProblemReq]` field; that is
a forward-compatible extension.**

#### 3.4.1 `LogType` enum

Defined in `app/api_types.py:49-77`. The bridge sends these uppercase
strings verbatim. The API MUST accept the full set:

| Value | Category |
|-------|----------|
| `TEMPERATURE` | sensor |
| `HUMIDITY` | sensor |
| `LIGHT` | sensor |
| `FAN` | sensor |
| `TANK_ML` | sensor (water level) |
| `TANK_LEVEL` | sensor |
| `PH_VALUE` | sensor |
| `PH_LEVEL` | sensor |
| `PH_ML` | sensor (dosing) |
| `SUPPLEMENT_ML` | sensor (dosing) |
| `SUPPLEMENT_LEVEL` | sensor |
| `PLANT_WATER` | sensor |
| `SOIL_MOISTURE` | sensor |
| `HEATER_STATE` | actuator state (binary) |
| `FAN_STATE` | actuator state (binary) |
| `HUMIDIFIER_STATE` | actuator state (binary) |
| `DEHUMIDIFIER_STATE` | actuator state (binary) |
| `LIGHT_STATE` | actuator state (binary) |
| `FAN_SPEED` | actuator state (variable) |
| `LIGHT_LEVEL` | actuator state (variable) |

Unknown values from custom integrations are passed through as the
caller specified — the API should accept any uppercase string but
SHOULD validate against this enum and reject obvious typos with 400.

#### 3.4.2 Example data batch

```json
POST /bridge/9d7e4c2a-3f9b-4f15-87b6-1a45c0d12345/data
X-Client-ID: 9d7e4c2a-3f9b-4f15-87b6-1a45c0d12345
Content-Type: application/json

{
  "dataLogs": [
    {
      "logDate": "2026-05-04T14:22:31.412000",
      "logType": "TEMPERATURE",
      "value": "23.6",
      "deviceId": "mqtt.tent_temp"
    },
    {
      "logDate": "2026-05-04T14:22:31.412000",
      "logType": "HUMIDITY",
      "value": "58.2",
      "deviceId": "mqtt.tent_humidity"
    },
    {
      "logDate": "2026-05-04T14:22:31.500000",
      "logType": "PH_VALUE",
      "value": "6.1",
      "deviceId": "http.tank_ph"
    }
  ]
}
```

### 3.5 `POST /bridge/{bridge_id}/actions/{action_id}/result` — action result

Source: `api_client.send_command_result` (`api_client.py:595-619`).

| Item    | Value                                                                |
|---------|----------------------------------------------------------------------|
| Method  | `POST`                                                               |
| Path    | `/bridge/{bridge_id}/actions/{action_id}/result`                     |
| Headers | Standard auth headers                                                |
| Body    | `{"success": bool, "message": "string", "timestamp": <ms-epoch int>}` |
| Response (success) | 200 OK; body ignored.                                     |

Fired exactly once per inbound `action` SSE event, after the bridge has
attempted the command via `integration.execute_command(target_id,
action, payload)` (`main.py:443-446`). On any exception in command
processing the bridge still posts a result with `success=false` and a
diagnostic message.

There is currently **no retry** on this path; it's a single best-effort
post. If it fails the API will time out the action on its own
expiration policy.

---

## 4. Inbound SSE event stream (API → bridge)

### 4.1 Connection

```
GET /bridge/{bridge_id}/stream?configVersion={local_version}
Accept: text/event-stream
X-Client-ID: {bridge_id}
```

`local_version` is the bridge's locally-stored `configVersion` (or `0`
if the bridge has never received a config). The API uses this to decide
whether to push a `config` event immediately on connect:

- If `local_version` < server version: the API SHOULD send a `config`
  event right away to bring the bridge up to date.
- If equal: no immediate `config`; the bridge has cached state.

The bridge maintains the stream open with a 90 s read timeout
(`SSE_STREAM_TIMEOUT`, `api_client.py:49`). On read timeout, network
error, or 5xx, the bridge reconnects with exponential backoff (§2.4).

### 4.2 Event types

The SSE parser in `api_client.py:721-779` recognises four `event:`
names. Any other event name is logged at DEBUG and dropped. The
`data:` line is always a single JSON object (multi-line `data:` is
joined with `\n` and JSON-parsed once).

#### 4.2.1 `event: connected`

Sent once immediately after the API accepts the SSE upgrade
(`_handle_connected_event`, `api_client.py:871-874`).

```json
{"configVersion": 7}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `configVersion` | int | yes | The current server-side config version. The bridge logs this for diagnostics; the actual sync of stored data happens on the subsequent `config` event (if any). |

#### 4.2.2 `event: config`

Full snapshot push (`_handle_config_event`, `api_client.py:781-815`).
The payload is the same shape as the body of `GET /bridge/{bridge_id}`
when status is 200 — call this `BridgeSpaceResp`.

| Field             | Type     | Required | Notes |
|-------------------|----------|----------|-------|
| `configVersion`   | int      | yes      | Monotonic per-bridge. Stored in `config_store` under key `full`. |
| `rdhMode`         | bool     | optional | "Run-Dry-Harvest" mode flag passed through to integration `apply_settings`. |
| `status`          | string   | optional | Free-form lifecycle status from the API (e.g. `"GROWING"`). |
| `light`           | object   | optional | Light schedule; structure consumed by integrations. Typical: `{"day": "06:00-18:00", "night": "...", "intensity": ...}`. |
| `climate`         | object   | optional | Climate setpoints. Typical: `{"temperature": 24.5, "humidity": 60, "baseFanSpeed": 30}`. |
| `tank`            | object   | optional | Tank/dosing config. Typical: `{"waters": [...], "ph": {...}, "amountML": ...}`. |
| `actions`         | array    | optional | Pending one-shot actions. Same shape as the `action` event payload (§4.2.4). The bridge currently does not act on these — actions are processed only when delivered as their own `event: action`. |
| `deviceAssignments` | array  | optional | **The role-assignment list — see below.** |

**`deviceAssignments` shape** (`config_store.save_device_assignments`,
`config_store.py:217-232`; consumer at `web/app.py:398-432`):

```json
"deviceAssignments": [
  {"entityId": "gpio.water_pump", "role": "WATER_PUMP", "slot": 1},
  {"entityId": "mqtt.tent_temp",  "role": "TEMPERATURE_SENSOR", "slot": null},
  {"entityId": "gpio.spare_pump", "role": "IGNORED", "slot": null}
]
```

Each entry is `{entityId: string, role: string, slot: int|null}`.

- `entityId` MUST be a `<domain>.<name>` that the bridge has previously
  pushed in a manifest. The API is responsible for validation.
- `role` is one of the `GrowRole` enum values (§6) as a string.
- `slot` is required for `MULTIPLE`-cardinality roles (e.g.
  `CIRCULATION_FAN` slot=1, slot=2). For `SINGLETON` roles `slot` is
  `null` or omitted.

**The bridge treats this list as display-only.** Command routing always
goes by `entityId` via `registry.get_*_integration` — never by role
(`web/app.py:790`, doc-comment at `config_store.py:215`). Operators see
"WATER_PUMP" labels on the bridge web UI; that's the only effect.

If the field is missing or not a list, the bridge logs a warning and
treats it as an empty list. The list is wholly replacing — every
`config` event publishes the complete current set, not a delta.

#### 4.2.3 `event: heartbeat`

Periodic keep-alive every ~15 s (the value isn't enforced bridge-side;
the bridge only uses `SSE_STREAM_TIMEOUT=90` to detect a *missing*
heartbeat). Source: `_handle_heartbeat_event`, `api_client.py:832-869`.

```json
{
  "ts": 1746375751412,
  "configVersion": 7,
  "manifestHash": "e82f0160f741cd2d64e80a315abf0d4014efafc64865a875f6e9971880e77af3"
}
```

| Field          | Type | Required | Notes |
|----------------|------|----------|-------|
| `ts`           | int  | optional | Server epoch milliseconds. Logged by the bridge; not used for clock sync. |
| `configVersion`| int  | yes      | Drift check. If `≠ local_version`, the bridge fires `fetch_full_config()` (a synchronous `GET /bridge/{bridge_id}`) to resync, then re-runs `_apply_settings`. |
| `manifestHash` | string | optional | Drift check. If present and `≠ stored_manifest_hash`, the bridge schedules a fresh `send_manifest()`. |

The two drift checks are independent and either or both may fire on a
single heartbeat.

#### 4.2.4 `event: action`

A command targeted at one device. Source: `_handle_action_event`,
`api_client.py:817-830`; consumed by `_process_command`, `main.py:404-451`.

```json
{
  "id": "act_8f2c...",
  "type": "TANK_ML",
  "targetType": "actuator",
  "targetId": "gpio.water_pump",
  "action": "on",
  "payload": {"durationMs": 12000, "amountML": 250}
}
```

| Field        | Type   | Required | Notes |
|--------------|--------|----------|-------|
| `id`         | string | yes      | API-generated UUID. The bridge echoes it back in the result POST (§3.5). If absent, the bridge drops the event. |
| `type`       | string | optional | Mirrors `ActionType` (§3.4.1). The bridge does not route on this — it's only logged. The new API can rely on `targetType`/`targetId` to identify the device. |
| `targetType` | string | yes      | `"sensor"` or `"actuator"`. Drives the registry lookup table (`get_sensor_integration` vs `get_actuator_integration`, `main.py:424-433`). |
| `targetId`   | string | yes      | The `entity_id` of the target device. For backward-compat the registry's legacy index also accepts a bare `name`, but new APIs SHOULD always send the fully qualified `entity_id`. |
| `action`     | string | yes      | The command verb. Common values: `"on"`, `"off"`, `"set"`, `"speed"`, `"temperature"`, `"level"`. The integration interprets these. |
| `payload`    | object | optional | Free-form parameters. Passed verbatim to `integration.execute_command(target_id, action, payload)`. |

**Routing flow:**

1. `_handle_action_event` puts the raw payload onto an internal queue.
2. `_command_execution_task` (`main.py:370-402`) drains the queue.
3. `_process_command` looks up the integration:
   - `targetType == "sensor"`: `registry.get_sensor_integration(targetId)`
   - `targetType == "actuator"`: `registry.get_actuator_integration(targetId)`
4. The integration's `execute_command(target_id, action, payload)` is awaited.
5. The bridge POSTs `/bridge/{id}/actions/{action_id}/result` with success/failure.

If the target is unknown or has no integration, the bridge replies with
`success=false` and message `"No integration for {targetType} {targetId}"`.

---

## 5. Manifest payload reference

### 5.1 Schema

Top-level (`registry.serialize_manifest`, `registry.py:220-247`):

| Field             | Type    | Required | Notes |
|-------------------|---------|----------|-------|
| `manifestVersion` | int     | yes      | Monotonically increasing per-bridge. See §3.3 for versioning semantics. |
| `generatedAt`     | string  | yes      | ISO-8601 UTC with `Z` suffix, generated at serialize time (`datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`). Informational; not used for ordering. |
| `devices`         | array   | yes      | One entry per registered device. Order is alphabetical by `entityId` (`registry.py:229`). |

Each device entry:

| Field             | Type     | Required | Notes |
|-------------------|----------|----------|-------|
| `entityId`        | string   | yes      | `<domain>.<name>`. Stable identifier (§7). |
| `domain`          | string   | yes      | Lowercase. The integration's class name with trailing `Integration` stripped, e.g. `"gpio"`, `"mqtt"`, `"http"`. Custom integrations can override via `register_device(domain=...)`. |
| `name`            | string   | yes      | The device's local name within its domain. Free-form, must be unique per domain. |
| `deviceType`      | string   | yes      | Free-form type string. Common values: `"pump"`, `"fan"`, `"light"`, `"heater"`, `"humidity"`, `"temperature"`, `"water_level"`, `"light_sensor"`, `"ph"`, `"ec"`, `"pressure"`, `"flow"` (`registry.py:78-91`). The taxonomy is open; custom integrations can introduce their own types. |
| `category`        | string   | yes      | **Uppercase.** One of `"SENSOR"` or `"ACTUATOR"`. The bridge stores the `DeviceCategory` enum lowercase but uppercases on serialize (`registry.py:210`, `registry.py:237`). |
| `integrationName` | string   | yes      | The integration's class name (e.g. `"GPIOIntegration"`). Used by the bridge for routing; the API can store it as opaque metadata. |
| `capabilities`    | string[] | yes      | Sorted in the *hash* but emitted in registration order in the manifest (`registry.py:212` vs `:239`). The list of action verbs the device supports — typical values for a `"pump"` are `["on", "off"]`. |
| `metadata`        | object   | yes      | Free-form; defaults to `{}`. Whatever the integration passed to `register_device(metadata=...)`. |

**Important deviation between hash and serialized form:**

- For *hashing* (`compute_manifest_hash`, `registry.py:192-218`), `capabilities` is **sorted** and `metadata` is **excluded**.
- For *serialization* (`serialize_manifest`), `capabilities` is the registration-order list and `metadata` is included.

This means changes to `metadata` do **not** invalidate the manifest
hash. The new API's hash computation MUST follow the same rule (see
§5.2).

### 5.2 Manifest hash algorithm

Source: `registry.py:192-218`. Used by the heartbeat drift check (§4.2.3)
and SHOULD also be computed independently by the API to verify integrity.

Pseudocode:

```python
items = []
for entity_id in sorted(devices.keys()):
    d = devices[entity_id]
    payload = {
        "entityId": entity_id,
        "domain": d.domain,
        "name": d.name,
        "deviceType": d.device_type,
        "category": d.category.upper(),       # "SENSOR" or "ACTUATOR"
        "integrationName": d.integration_name,
        "capabilities": sorted(d.capabilities),
    }
    items.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
hash = sha256("\n".join(items).encode("utf-8")).hexdigest()
```

Byte-exact requirements for the API implementation:

1. Iterate devices in **ASCII-sorted** `entityId` order.
2. Build the payload object with exactly the seven keys above, in any
   order — `sort_keys=True` will canonicalize.
3. Use compact JSON separators: `(",", ":")` — **no spaces**.
4. `category` is uppercase.
5. `capabilities` is **sorted** ASCII order.
6. `metadata` is **not** in the hash input.
7. Join the per-device JSON strings with `\n` (LF, no trailing newline).
8. Encode UTF-8, SHA-256, hex digest, lowercase.

#### 5.2.1 Verified fixture

The following minimal two-device registry produces a deterministic hash
that the new API can use as a unit-test sanity check.

Devices:

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

Hash input (two lines, joined with one `\n`):

```
{"capabilities":["off","on"],"category":"ACTUATOR","deviceType":"pump","domain":"gpio","entityId":"gpio.pump1","integrationName":"GPIOIntegration","name":"pump1"}
{"capabilities":[],"category":"SENSOR","deviceType":"temperature","domain":"mqtt","entityId":"mqtt.temp1","integrationName":"MQTTIntegration","name":"temp1"}
```

Expected SHA-256 hex digest:

```
f5b1954d657d7247d578bd15ff4e4bca827986bd88bc1c6a086886ac0ed158df
```

Verified by running the bridge's `compute_manifest_hash` algorithm
end-to-end on 2026-05-04 against this codebase. If the API's
implementation produces a different digest for this input, the
implementation is wrong and the bridge will erroneously re-push on
every heartbeat.

### 5.3 Example manifest

```json
POST /bridge/9d7e4c2a-3f9b-4f15-87b6-1a45c0d12345/manifest
X-Client-ID: 9d7e4c2a-3f9b-4f15-87b6-1a45c0d12345
Content-Type: application/json

{
  "manifestVersion": 12,
  "generatedAt": "2026-05-04T14:22:00.123456Z",
  "devices": [
    {
      "entityId": "gpio.water_pump",
      "domain": "gpio",
      "name": "water_pump",
      "deviceType": "pump",
      "category": "ACTUATOR",
      "integrationName": "GPIOIntegration",
      "capabilities": ["on", "off"],
      "metadata": {"pin": 17, "active_high": true}
    },
    {
      "entityId": "mqtt.tent_temp",
      "domain": "mqtt",
      "name": "tent_temp",
      "deviceType": "temperature",
      "category": "SENSOR",
      "integrationName": "MQTTIntegration",
      "capabilities": [],
      "metadata": {"topic": "tent/sensors/temp"}
    }
  ]
}
```

Successful response:

```json
HTTP/1.1 200 OK
Content-Type: application/json

{"acceptedVersion": 12}
```

### 5.4 Triggers, idempotency, and soft-removal

A manifest push happens on:

- **Startup**, after integrations populate the registry.
- **Registry change**: any `register_device` (or eventual remove) fires
  `_on_registry_change` → schedules `send_manifest()` on the loop.
- **Hash drift**: a heartbeat reporting a `manifestHash` different
  from the bridge's local one schedules a re-push.

The bridge does **not** push periodically as a heartbeat — the API can
rely on heartbeat-echoed `manifestHash` as the freshness signal.

**Soft removal:** The bridge does not currently call any "remove" path
on the API when a device disappears from a manifest. The expected API
behaviour is: any `BridgeDevice` row for `(bridge_id, entity_id)` that
is **not present** in the latest accepted manifest is marked
`removed=true` (soft delete). If the same `entity_id` re-appears in a
later manifest, the same row is reactivated (`removed=false`) — never
duplicated. This preserves historical `lastSeen`, role assignments, and
audit history across transient hardware drop-outs.

---

## 6. Role taxonomy (`GrowRole`)

The bridge does not interpret roles — it only stores them as labels for
the web UI (§4.2.2). However, the API and bridge must agree on the role
*taxonomy* because the `deviceAssignments` payload uses these strings.

The full enum (mirrors the existing API at
`KweekVad3rAPI/.../GrowRole.java` for reference; the new API MUST
implement an equivalent):

| Role | Compatible `deviceType` | Cardinality |
|------|-------------------------|-------------|
| `WATER_PUMP` | `pump` | SINGLETON |
| `PH_UP_PUMP` | `pump` | SINGLETON |
| `PH_DOWN_PUMP` | `pump` | SINGLETON |
| `NUTRIENT_A_PUMP` | `pump` | SINGLETON |
| `NUTRIENT_B_PUMP` | `pump` | SINGLETON |
| `NUTRIENT_C_PUMP` | `pump` | SINGLETON |
| `INTAKE_FAN` | `fan` | SINGLETON |
| `EXHAUST_FAN` | `fan` | SINGLETON |
| `CIRCULATION_FAN` | `fan` | MULTIPLE |
| `MAIN_LIGHT` | `light` | SINGLETON |
| `HEATER` | `heater` | SINGLETON |
| `HUMIDIFIER` | `humidity` | SINGLETON |
| `DEHUMIDIFIER` | `humidity` | SINGLETON |
| `TEMPERATURE_SENSOR` | `temperature` | SINGLETON |
| `HUMIDITY_SENSOR` | `humidity` | SINGLETON |
| `PH_SENSOR` | `ph` | SINGLETON |
| `EC_SENSOR` | `ec` | SINGLETON |
| `WATER_LEVEL_SENSOR` | `water_level` | SINGLETON |
| `WATER_TEMPERATURE_SENSOR` | `temperature` | SINGLETON |
| `SOIL_MOISTURE_SENSOR` | `soil_moisture` | MULTIPLE |
| `CO2_SENSOR` | `co2` | SINGLETON |
| `AMBIENT_LIGHT_SENSOR` | `illuminance`, `par` | SINGLETON |
| `PRESSURE_SENSOR` | `pressure` | MULTIPLE |
| `FLOW_SENSOR` | `flow` | MULTIPLE |
| `IGNORED` | (any) | MULTIPLE |
| `UNASSIGNED` | (any) | MULTIPLE |

**Cardinality rules (the API enforces, not the bridge):**

- **SINGLETON**: at most one `BridgeDevice` per space may carry this
  role at a time. Assigning a SINGLETON role to a second device must
  either reject (preferred) or auto-unassign the previous holder.
- **MULTIPLE**: any number of devices may carry this role; `slot`
  disambiguates them (`CIRCULATION_FAN` slot=1 vs slot=2).

**`IGNORED` vs `UNASSIGNED`:**

- `UNASSIGNED` is the default state for a freshly-discovered device. It
  is not actionable; the operator hasn't decided what it is yet.
- `IGNORED` is an explicit operator decision: "this device exists but I
  don't want it to participate in any automation." The API should never
  surface IGNORED devices in role-driven UI flows but MUST keep them in
  the inventory and continue accepting telemetry from them.

Both accept any `deviceType`.

### 6.1 Compatibility validation

The API SHOULD reject an assignment whose role's
`compatibleDeviceTypes` does not contain the device's `deviceType` —
unless the role is `IGNORED` or `UNASSIGNED`. (Reject = 400 Bad
Request, body explaining the conflict.)

### 6.2 Cardinality reconciliation across manifests

If a new manifest arrives that introduces a second device with a
SINGLETON role's required `deviceType`, the API must **not** silently
duplicate the role. Recommended behaviour:

- Leave existing assignments untouched.
- New devices come in with `role=UNASSIGNED`.
- The operator resolves the conflict via the consumer app.

The "most-recent manifest wins" rule applies only to the **inventory**
(which devices physically exist). Role assignments are operator-managed
state owned by the API and survive across manifests.

---

## 7. Stable identity rules

### 7.1 `entity_id = <domain>.<name>`

Every registered device has an `entity_id` that is the dotted
concatenation of its `domain` and `name`. Source:
`DeviceInfo.entity_id` property (`registry.py:39-42`) and
`register_device` (`registry.py:106`).

- `domain` is lowercase.
- `name` is whatever the integration passed in. By convention it does
  not contain dots, but the bridge does not enforce this — only the
  *first* dot matters for parsing.
- The pair `(bridge_id, entity_id)` is the **stable primary key** the
  API should use for any `BridgeDevice` row.

### 7.2 Domain derivation

When an integration uses the convenience methods
`registry.register_sensor` / `register_actuator` without specifying a
`domain` argument, the bridge derives it from the integration class
name (`registry.py:249-254`):

- Strip a trailing `Integration` suffix (case-sensitive, exactly that
  literal).
- Lowercase the remainder.

Examples:

| Integration class | Default domain |
|-------------------|----------------|
| `GPIOIntegration` | `gpio` |
| `MQTTIntegration` | `mqtt` |
| `HTTPIntegration` | `http` |
| `SerialIntegration` | `serial` |
| `MyCustomIntegration` | `mycustom` |
| `DHTSensor` (no suffix) | `dhtsensor` |

Custom integrations should pass `domain=...` explicitly when the derived
default would collide or when human-friendly naming is preferred.

### 7.3 Stability across restarts

Because the registry is rebuilt from `config.yaml` on every bridge
start, an `entity_id` is stable iff:

- The integration's class name is unchanged (or the explicit `domain`
  override is unchanged), **and**
- The device's `name` in `config.yaml` is unchanged.

The bridge does not persist registered devices across restarts in
SQLite — the `config_store` only persists API-pushed config, manifest
version/hash, and outbound queue. The registry is ephemeral. The API,
however, MUST persist `BridgeDevice` rows; an `entity_id` that
disappears for a few minutes (during a bridge restart) and re-appears
in the next manifest is the **same** logical device.

### 7.4 Re-appearance and re-use

When a previously-removed `entity_id` reappears in a manifest, the API
should reactivate the existing `BridgeDevice` row (`removed=false`)
rather than create a new row. Existing role assignments, lastSeen, and
historical telemetry remain attached.

---

## 8. Liveness model

The bridge does not push a dedicated "I'm alive" signal per device.
Instead, **`lastSeen` on a `BridgeDevice` is updated as a side effect
of telemetry**:

- On every `POST /bridge/{id}/data`, for every `DataLogReq` whose
  `deviceId` matches an existing `(bridge_id, entity_id)` row, the API
  MUST set `lastSeen = now()`.
- A `DataLogReq` without a `deviceId` MUST NOT touch any device's
  `lastSeen`.
- A manifest push **does not** count as liveness. A device that
  appears in the manifest but never produces telemetry is "registered
  but never seen".

### 8.1 Stale flip threshold

Recommended threshold: **3 minutes**. If `now() - lastSeen > 3 min`,
the device is `stale=true` for UI purposes. The bridge does not consume
this flag — it's purely an API-side derived field.

The threshold should be configurable per-deployment but a single global
default is sufficient for v1.

### 8.2 Recovery

A previously-stale device transitions back to fresh on the next
telemetry that touches it. There is no separate recovery signal; the
liveness state is purely a function of `lastSeen`.

---

## 9. Multi-pod / failure-mode notes

### 9.1 Reconnect resync

On every SSE reconnect, the bridge sends `?configVersion={local}`. The
API uses this to decide whether to push a fresh `config` event. A
correctly implemented API guarantees that after the SSE handshake, the
bridge's local config is at least as new as the API's, so post-restart
the bridge boots with the last-known-good config from `config_store`
and re-syncs as soon as the SSE comes up.

If the bridge lost connectivity for an extended period and missed
multiple `config` versions, the resync is still a single push — the API
sends the *current* full config, not a delta replay.

### 9.2 Manifest hash drift

If multiple API pods serve heartbeats, they must agree on the
`manifestHash` they advertise. The recommended implementation:

- The "primary" record of accepted `manifestVersion` and content hash
  lives in the API's persistence layer.
- Heartbeats fetch the hash from there (or from a cache populated on
  manifest acceptance).
- Two pods returning different hashes for the same `bridge_id` will
  cause the bridge to thrash (re-push on every heartbeat from the
  laggy pod). This is observable as repeated manifest pushes in the
  bridge log; the API team should alert on it.

### 9.3 Bridge restart

On restart the bridge:

1. Loads `credentials.json` → has `bridge_id`.
2. Loads the latest cached `config` from `config_store` and applies it
   to integrations before SSE is up (`main.py:182-196`). This means
   pump schedules etc. continue running through the gap.
3. Reads `manifest_version` and `manifest_hash` from `config_store`
   (it does not push a manifest yet; that happens after integrations
   load).
4. Loads integrations, populates registry, pushes manifest with
   `manifest_version + 1`.
5. Opens SSE; first event tells it whether the API's view matches.

The API can rely on **monotonic `manifestVersion`** as the only ordering
signal. A push with `manifestVersion <= latestAccepted` is stale and
should be rejected (with `acceptedVersion` echoed; see §3.3).

### 9.4 Cardinality conflict reconciliation

If an operator assigns `WATER_PUMP` to device A, and a new manifest
introduces device B with `deviceType=pump`, the new device comes in as
`UNASSIGNED`. The existing `WATER_PUMP=A` assignment is untouched. The
"most-recent manifest wins" rule applies only to the **inventory list**
(which devices exist), not to role assignments — those are operator-
managed state.

---

## 10. Glossary

- **`bridge_id`** — UUID issued by the API at pairing time (§2.1),
  persisted by the bridge in `data/credentials.json`. Used in URL paths
  and the `X-Client-ID` header.
- **`entity_id`** — `<domain>.<name>` string. The stable identifier
  for a device managed by the bridge. See §7.
- **`manifestVersion`** — Monotonic per-bridge integer. Bumped by 1
  on each manifest push. The API echoes the accepted version (§3.3).
- **`manifestHash`** — SHA-256 hex of a deterministic serialization
  of the device set. Used for drift detection on heartbeats. See §5.2.
- **`configVersion`** — Monotonic per-bridge integer issued by the
  API. Stored alongside the cached `BridgeSpaceResp`. Drift-checked
  on heartbeats; resync request sent on SSE reconnect.
- **`GrowRole`** — Enum of operator-meaningful role names (e.g.
  `WATER_PUMP`, `EXHAUST_FAN`). Bridge stores assignments display-only;
  API enforces cardinality. See §6.
- **`BridgeDevice`** — API-side persistent row keyed by
  `(bridge_id, entity_id)`. Holds `lastSeen`, `removed`, role assignment.
  Bridge does not see this concept directly.
- **`deviceAssignment`** — `{entityId, role, slot}` triplet. Lives in
  the `config` event payload. Bridge stores in `config_store` for the
  web UI. See §4.2.2.

---

## 11. Open questions / future contract notes

The following are deliberately *not* nailed down in this spec; the API
team should confirm before the new API ships.

1. **HMAC authentication.** Today the bridge presents `X-Client-ID`
   only. Anyone who learns a `bridge_id` can impersonate the bridge.
   The recommended hardening is HMAC-signed requests (key issued at
   pairing; signature over method + path + body + nonce). The bridge
   already has `Authorization: Bearer` plumbing via `build_auth_headers`;
   adding an HMAC signer is a one-file change. The new API SHOULD
   enforce this from day one and reject pre-HMAC bridges with a
   loud-failure status. Plan: ship the new API with both modes
   tolerated, then flip after a deprecation window.

2. **Manifest re-push on soft-reject.** Today, a 4xx response to
   `/manifest` causes the bridge to leave its local
   `manifest_version` unchanged, so the next change re-tries with the
   *same* `next_version = current + 1` (i.e. it doesn't bump on
   failure). If the API wants to communicate "I have a higher
   accepted version, please use mine," the recommended response is
   `409 Conflict {"acceptedVersion": N}`, and the bridge should write
   that back. The current bridge code does **not** do this on 4xx —
   it treats the push as failed and the version is not advanced. This
   needs a small bridge change to be fully bidirectional.

3. **Multi-bridge per space.** The current contract carries no
   notion of "which bridge sent this telemetry" beyond the URL path's
   `bridge_id`. If a single space ever accepts multiple bridges (e.g.
   one main + one secondary in another room), the API will need to
   namespace `entity_id` by `bridge_id` in any cross-space view.
   Confirm: is this in-scope for v1 of the new API or explicitly out?

4. **Problem reporting wire format.** The post-Phase-5 `/data` body
   only carries `dataLogs`. The bridge still detects out-of-range
   values and sensor failures internally
   (`_detect_problems_from_data`) but does not currently send them.
   Decide: (a) drop entirely, (b) re-introduce `problems: [...]` in
   `/data`, or (c) add a separate `/problems` endpoint. This doc
   currently assumes (a).

5. **Action expiration / retry.** The bridge POSTs the action result
   exactly once with no retry (`api_client.py:595-619`). What's the
   API's expiration policy for actions whose result never arrives?
   Should the bridge re-deliver if it sees the same `action_id`
   redelivered on SSE? Today it would re-execute — there's no
   dedup cache.

6. **`type` field in the `action` event.** The bridge currently
   ignores it for routing (uses `targetType`/`targetId` only). The new
   API should either remove it or specify what it means; today it's
   essentially documentation.

7. **`generatedAt` use.** The bridge fills it in but the bridge does
   not use it. Should the API treat it as authoritative (e.g. as a
   tiebreaker for two manifests received with the same
   `manifestVersion` due to a clock skew)? Recommended: no — rely on
   `manifestVersion` only.

8. **`metadata` semantics.** It's free-form, not in the manifest hash,
   and integration-dependent (e.g. `{"pin": 17}` for GPIO,
   `{"topic": "..."}` for MQTT). Should the API persist it? Today
   nothing on the API depends on it; it's primarily for the bridge UI
   and operator debugging.
