"""MQTT transport for communicating with the GrowAssistant app.

This is the sole transport to the app (the old REST + SSE ``ApiClient`` has
been removed). Manifest/state/telemetry/command-acks/automations-status are
published to the broker; commands, automations, and WebRTC offers are
subscribed.

Threading model
---------------
paho runs its network loop in a background thread (``loop_start``). All of
paho's callbacks (``on_connect`` / ``on_message`` / ``on_disconnect``) fire on
that thread, NOT the asyncio loop. We capture the asyncio loop in ``start()``
and hand work back to it with ``run_coroutine_threadsafe`` /
``call_soon_threadsafe`` — never touching the asyncio ``Queue`` or the
SQLite-backed ``config_store`` directly from paho's thread.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.auth import auth_manager
from app.config import config
from app.config_store import config_store
from app.entity_id import derive_domain
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

DEFAULT_MQTT_KEEPALIVE = 60
DEFAULT_MQTT_PORT = 1883
# Interval (s) for the maintainer task that connects once creds appear.
MAINTAINER_INTERVAL = 5.0


class MqttTransport(metaclass=SingletonMeta):
    """MQTT client for the GrowAssistant app.

    Publishes manifest/state/telemetry/command-acks and subscribes to commands
    and automations. Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the MQTT transport."""
        self._client: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._command_queue: Optional[asyncio.Queue] = None
        self._manifest_lock: Optional[asyncio.Lock] = None

        self._connected: bool = False
        self._running: bool = False
        self._maintainer_task: Optional[asyncio.Task] = None
        self._refresh_in_progress: bool = False

        self._settings_callback: Optional[Callable] = None
        self._automations_callback: Optional[Callable] = None
        self._webrtc_callback: Optional[Callable] = None
        self._keepalive = config.get("api.mqtt_keepalive", DEFAULT_MQTT_KEEPALIVE)

        logger.info("MQTT transport initialized")

    # ─── Topic helpers ──────────────────────────────────────────────

    def _topic_prefix(self) -> Optional[str]:
        """Return ``ga/{tenantId}/bridge/{bridgeId}/`` or None if not paired."""
        tenant_id = auth_manager.get_tenant_id()
        bridge_id = auth_manager.get_client_id()
        if not tenant_id or not bridge_id:
            return None
        return f"ga/{tenant_id}/bridge/{bridge_id}/"

    def _topic(self, suffix: str) -> Optional[str]:
        """Build a fully-qualified topic for the given suffix."""
        prefix = self._topic_prefix()
        return f"{prefix}{suffix}" if prefix else None

    # ─── Lifecycle ──────────────────────────────────────────────────

    async def start(self):
        """Start the transport: wire registry callback and spawn the maintainer.

        The maintainer task connects once credentials are present; it does not
        block startup if the bridge is unpaired.
        """
        self._command_queue = asyncio.Queue()
        self._manifest_lock = asyncio.Lock()
        self._running = True

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        from app.registry import registry as _registry

        _registry.add_change_callback(self._on_registry_change)

        self._maintainer_task = asyncio.create_task(self._maintainer_loop())
        logger.info("MQTT transport started")

    async def stop(self):
        """Stop the transport: publish offline state, disconnect, stop loop."""
        self._running = False

        try:
            from app.registry import registry as _registry

            _registry.remove_change_callback(self._on_registry_change)
        except Exception:
            logger.debug("Failed to deregister registry change callback", exc_info=True)

        if self._maintainer_task and not self._maintainer_task.done():
            self._maintainer_task.cancel()
            try:
                await self._maintainer_task
            except asyncio.CancelledError:
                pass
            self._maintainer_task = None

        if self._client:
            state_topic = self._topic("state")
            if state_topic and self._connected:
                try:
                    self._client.publish(
                        state_topic, json.dumps({"online": False}), qos=1, retain=True
                    )
                except Exception:
                    logger.debug("Failed to publish offline state", exc_info=True)
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                logger.debug("Error during MQTT disconnect", exc_info=True)
            self._client = None

        self._connected = False
        logger.info("MQTT transport stopped")

    def register_settings_callback(self, callback: Callable):
        """Store a settings callback. No-op channel in this slice (no settings
        topic), kept for API symmetry with ApiClient."""
        self._settings_callback = callback

    def register_automations_callback(self, callback: Callable):
        """Store the coroutine invoked with the raw bytes of an inbound
        ``…/automations`` message. Called on paho's thread via ``_schedule`` so
        it runs on the asyncio loop (it touches the SQLite-backed config_store
        and the registry)."""
        self._automations_callback = callback

    def register_webrtc_callback(self, callback: Callable):
        """Store the coroutine invoked with the JSON-decoded payload of an
        inbound ``…/webrtc/offer`` message. Called on paho's thread via
        ``_schedule`` so it runs on the asyncio loop. The callback performs the
        go2rtc SDP negotiation and publishes the answer via
        ``send_webrtc_answer``."""
        self._webrtc_callback = callback

    # ─── Connection management ──────────────────────────────────────

    async def _maintainer_loop(self):
        """Background task: connect once authenticated; reconnect if dropped."""
        logger.info("MQTT maintainer task started")
        try:
            while self._running:
                if auth_manager.is_authenticated() and not self._connected and self._client is None:
                    try:
                        await self._connect()
                    except Exception as e:
                        logger.error(f"MQTT connect attempt failed: {e}")
                await asyncio.sleep(MAINTAINER_INTERVAL)
        except asyncio.CancelledError:
            logger.info("MQTT maintainer task cancelled")
        logger.info("MQTT maintainer task stopped")

    async def _connect(self):
        """Create the paho client and connect with the current credentials.

        paho's ``connect()`` does blocking DNS+TCP, so we run it in an executor
        to avoid stalling the asyncio loop.
        """
        host, port = auth_manager.get_broker_host_port(DEFAULT_MQTT_PORT)
        if not host:
            logger.warning("No broker URL available; cannot connect")
            return

        bridge_id = auth_manager.get_client_id()
        token = auth_manager.get_token()

        # We own reconnection via the maintainer task, so disable paho's
        # built-in auto-reconnect — otherwise a dropped client keeps
        # reconnecting (re-subscribing / re-publishing) in parallel with a
        # fresh client the maintainer builds.
        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=bridge_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        client.username_pw_set(username=bridge_id, password=token)

        # LWT must be set before connect — broker drops it as offline on death.
        state_topic = self._topic("state")
        if state_topic:
            client.will_set(state_topic, json.dumps({"online": False}), qos=1, retain=True)

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        self._client = client

        logger.info(f"Connecting to MQTT broker {host}:{port} as {bridge_id}")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, client.connect, host, port, self._keepalive)
        except Exception:
            self._client = None
            raise
        client.loop_start()

    # ─── paho callbacks (run on paho's network thread) ──────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Handle CONNACK. Runs on paho's thread — schedule async work."""
        if reason_code != 0:
            name = getattr(reason_code, "getName", lambda: str(reason_code))()
            logger.warning(f"MQTT connect refused: {reason_code} ({name})")
            # "Not authorized" → refresh the token once, then reconnect.
            if self._is_not_authorized(reason_code):
                self._schedule(self._handle_auth_failure())
            return

        self._connected = True
        logger.info("MQTT connected")

        cmd_topic = self._topic("cmd/+")
        automations_topic = self._topic("automations")
        webrtc_offer_topic = self._topic("webrtc/offer")
        if cmd_topic:
            client.subscribe(cmd_topic, qos=1)
        if automations_topic:
            client.subscribe(automations_topic, qos=1)
        if webrtc_offer_topic:
            client.subscribe(webrtc_offer_topic, qos=1)

        # Publish state(online) + manifest on the asyncio loop (config_store is
        # SQLite, thread-affine to the loop).
        self._schedule(self.send_manifest())

    def _on_disconnect(self, client, userdata, *args):
        """Handle disconnect. Runs on paho's thread.

        ``loop_stop()`` joins the network thread, so it must NOT be called from
        this callback (it would be joining itself). We only flip the connected
        flag here and schedule the client teardown onto the asyncio loop so the
        maintainer can rebuild a fresh client.
        """
        self._connected = False
        logger.warning("MQTT disconnected")
        self._schedule(self._teardown_client())

    async def _teardown_client(self):
        """Stop the paho loop and drop the client (runs on the asyncio loop)."""
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.loop_stop()
            except Exception:
                logger.debug("Error stopping loop during teardown", exc_info=True)

    def _on_message(self, client, userdata, message):
        """Handle an inbound MQTT message. Runs on paho's thread."""
        topic = message.topic

        # Automations rule set (retained). Handled from the RAW bytes and before
        # JSON-decoding: the payload may be empty (the app clears the retained
        # message when the last automation is deleted), and the validator needs
        # the exact bytes to echo their SHA-256. We subscribe to `…/automations`
        # only, never `…/automations/status` (which we publish), so this branch
        # never sees the status echo.
        if topic.endswith("/automations"):
            if self._automations_callback is not None:
                self._schedule(self._automations_callback(message.payload))
            else:
                logger.debug(f"Received automations on {topic} but no callback registered")
            return

        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.error(f"Inbound MQTT message on {topic} has invalid JSON")
            return

        # Ignore our own ack echoes (cmd/+ also matches cmd/{id}/ack).
        if topic.endswith("/ack"):
            logger.debug(f"Ignoring own ack echo on {topic}")
            return

        # WebRTC offer (JSON, non-retained). The callback negotiates with go2rtc
        # and publishes the answer. We publish (never subscribe) webrtc/answer,
        # so there is no echo-loop concern. Must not fall through to /cmd/.
        if topic.endswith("/webrtc/offer"):
            if self._webrtc_callback is not None:
                self._schedule(self._webrtc_callback(payload))
            else:
                logger.debug(f"Received webrtc offer on {topic} but no callback registered")
            return

        if "/cmd/" in topic:
            self._enqueue_command(payload)
            return

        logger.debug(f"Unhandled inbound topic: {topic}")

    # ─── Threading bridges ──────────────────────────────────────────

    def _schedule(self, coro):
        """Schedule a coroutine onto the asyncio loop from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def _enqueue_command(self, command: dict) -> None:
        """Hand a received command to the asyncio command queue thread-safely."""
        loop = self._loop
        queue = self._command_queue
        if loop is None or loop.is_closed() or queue is None:
            return
        loop.call_soon_threadsafe(queue.put_nowait, command)

    async def _handle_auth_failure(self):
        """Refresh the token once, then let the maintainer reconnect."""
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            logger.info("MQTT auth failure — refreshing token")
            if self._client is not None:
                try:
                    self._client.loop_stop()
                except Exception:
                    logger.debug("Error stopping loop on auth failure", exc_info=True)
                self._client = None
            self._connected = False
            await auth_manager.refresh_token()
        finally:
            self._refresh_in_progress = False

    @staticmethod
    def _is_not_authorized(reason_code) -> bool:
        """Detect a 'Not authorized' CONNACK across paho reason-code shapes."""
        # paho V2 ReasonCode exposes getName(); fall back to int/str compare.
        try:
            if int(reason_code) == 5:
                return True
        except (TypeError, ValueError):
            pass
        name = getattr(reason_code, "getName", lambda: str(reason_code))()
        return "not authorized" in str(name).lower()

    # ─── Registry change → manifest re-push ─────────────────────────

    def _on_registry_change(self) -> None:
        """Schedule a manifest+state publish when the device set changes."""
        if not auth_manager.is_authenticated() or not self._connected:
            return
        self._schedule(self.send_manifest())

    # ─── Publishing ─────────────────────────────────────────────────

    async def send_manifest(self) -> tuple[bool, str]:
        """Publish the retained manifest, bump/persist version+hash, publish state.

        Serialized under an async lock so concurrent callers (startup, registry
        callback, on_connect) don't double-bump the version.
        """
        if not self._connected or not self._client:
            return False, "MQTT not connected"

        manifest_topic = self._topic("manifest")
        state_topic = self._topic("state")
        if not manifest_topic or not state_topic:
            return False, "Not paired"

        if self._manifest_lock is None:
            self._manifest_lock = asyncio.Lock()

        from app.registry import registry

        async with self._manifest_lock:
            next_version = config_store.get_manifest_version() + 1
            payload = registry.serialize_manifest(next_version)
            manifest_hash = registry.compute_manifest_hash()

            try:
                self._client.publish(manifest_topic, json.dumps(payload), qos=1, retain=True)
                config_store.set_manifest_version(next_version)
                config_store.set_manifest_hash(manifest_hash)
                self._publish_state(state_topic, manifest_hash, next_version)
                logger.info(
                    f"Manifest published: v{next_version}, "
                    f"{len(payload['devices'])} devices, hash={manifest_hash[:12]}…"
                )
                return True, f"Manifest v{next_version} published"
            except Exception as e:
                logger.exception(f"Error publishing manifest: {e}")
                return False, f"Publish error: {e}"

    def _publish_state(self, state_topic: str, manifest_hash: str, manifest_version: int) -> None:
        """Publish the retained liveness/state message."""
        self._client.publish(
            state_topic,
            json.dumps(
                {
                    "online": True,
                    "manifestHash": manifest_hash,
                    "manifestVersion": manifest_version,
                }
            ),
            qos=1,
            retain=True,
        )

    async def send_data(
        self, data_points: Optional[list[dict[str, Any]]] = None
    ) -> tuple[bool, str]:
        """Publish telemetry built from queued data points (qos1, not retained)."""
        if not self._connected or not self._client:
            return False, "MQTT not connected"

        telemetry_topic = self._topic("telemetry")
        if not telemetry_topic:
            return False, "Not paired"

        samples = []
        for point in data_points or []:
            entity_id = self._derive_entity_id(point)
            if not entity_id:
                continue
            samples.append(
                {
                    "entityId": entity_id,
                    "value": point.get("value"),
                    "ts": self._iso_ts(point.get("timestamp")),
                }
            )

        if not samples:
            return True, "No samples to send"

        try:
            self._client.publish(
                telemetry_topic, json.dumps({"samples": samples}), qos=1, retain=False
            )
            logger.info(f"Telemetry published: {len(samples)} samples")
            return True, f"Sent {len(samples)} samples"
        except Exception as e:
            logger.exception(f"Error publishing telemetry: {e}")
            return False, f"Publish error: {e}"

    async def send_command_result(self, command_id: str, success: bool, message: str) -> bool:
        """Publish a command result to ``cmd/{id}/ack``."""
        if not self._connected or not self._client:
            logger.error("MQTT not connected; cannot send command result")
            return False

        ack_topic = self._topic(f"cmd/{command_id}/ack")
        if not ack_topic:
            return False

        payload = {
            "id": command_id,
            "success": success,
            "message": message,
            "ts": self._now_ms(),
        }
        try:
            self._client.publish(ack_topic, json.dumps(payload), qos=1, retain=False)
            logger.info(f"Command result sent: {command_id}, success={success}")
            return True
        except Exception as e:
            logger.error(f"Error sending command result: {e}")
            return False

    async def send_webrtc_answer(self, answer: dict[str, Any]) -> bool:
        """Publish a WebRTC answer to ``webrtc/answer`` (qos1, not retained).

        ``answer`` is the full payload the app expects — including the echoed
        ``sessionId`` and either ``{ok: True, sdp}`` or ``{ok: False, error}``.
        Always called (success or failure) so the app's awaited answer doesn't
        time out on errors.
        """
        if not self._connected or not self._client:
            logger.error("MQTT not connected; cannot send webrtc answer")
            return False

        answer_topic = self._topic("webrtc/answer")
        if not answer_topic:
            return False

        try:
            self._client.publish(answer_topic, json.dumps(answer), qos=1, retain=False)
            logger.info(
                f"WebRTC answer sent: session={answer.get('sessionId')}, ok={answer.get('ok')}"
            )
            return True
        except Exception as e:
            logger.error(f"Error sending webrtc answer: {e}")
            return False

    async def publish_automations_status(self, status: dict[str, Any]) -> bool:
        """Publish the rule-set validation/apply result to the retained
        ``…/automations/status`` topic (the round-trip the app uses to tell
        "saved" from "confirmed by the bridge")."""
        if not self._connected or not self._client:
            return False

        status_topic = self._topic("automations/status")
        if not status_topic:
            return False

        try:
            self._client.publish(status_topic, json.dumps(status), qos=1, retain=True)
            logger.info(
                f"Automations status published: ok={status.get('ok')}, count={status.get('count')}"
            )
            return True
        except Exception as e:
            logger.exception(f"Error publishing automations status: {e}")
            return False

    # ─── Command queue (consumed by main.py) ────────────────────────

    async def get_command(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Get a command from the queue. Returns None on timeout."""
        if self._command_queue is None:
            return None
        try:
            if timeout is None:
                return await self._command_queue.get()
            return await asyncio.wait_for(self._command_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    # ─── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _derive_entity_id(point: dict[str, Any]) -> Optional[str]:
        """Derive a stable ``<domain>.<name>`` entity_id from a telemetry point.

        The domain half is derived by the shared
        :func:`app.entity_id.derive_domain` — the same helper the manifest side
        (``registry.py``) uses, so telemetry and manifest never disagree on the
        domain. Each integration yields a different key for the device name, so
        we probe a series of keys in order of specificity. Returns None when no
        name can be found.
        """
        explicit = point.get("entity_id")
        if isinstance(explicit, str) and "." in explicit:
            return explicit

        integration = point.get("integration")
        if not integration:
            return None
        domain = derive_domain(integration)

        name = (
            point.get("device_id")
            or point.get("device")
            or point.get("entity_id")
            or point.get("sensor")
            or point.get("endpoint_name")  # HTTP integration
            or point.get("topic")  # MQTT integration
            or point.get("pin_name")  # GPIO integration
            or point.get("name")
            or point.get("target")
        )
        return f"{domain}.{name}" if name else None

    @staticmethod
    def _iso_ts(timestamp_ms: Optional[int]) -> str:
        """Convert a ms-epoch timestamp to ISO-8601 UTC (Z-suffixed)."""
        if timestamp_ms is None:
            dt = datetime.now(timezone.utc)
        else:
            dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _now_ms() -> int:
        """Current time in ms epoch."""
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def is_connected(self) -> bool:
        """Return whether the MQTT client is currently connected."""
        return self._connected

    @property
    def connected(self) -> bool:
        """Whether the MQTT client is currently connected."""
        return self._connected


# Create a global instance for easy imports
mqtt_transport = MqttTransport()
