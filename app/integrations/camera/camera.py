"""Camera Integration Implementation.

Supervises a local ``go2rtc`` process and brokers WebRTC SDP between the
GrowAssistant app (over MQTT, via ``main.py``) and go2rtc's HTTP API.

Signalling flow (de-risked, do not change)
------------------------------------------
The browser produces an SDP offer, the app forwards it here, and we
``POST http://<host>:<port>/api/webrtc?src=<streamId>`` with body
``{"type":"offer","sdp":<offer>}``. go2rtc replies (HTTP 200) with
``{"type":"answer","sdp":<answer>}`` whose answer already embeds go2rtc's ICE
candidates (non-trickle). We return that answer SDP for the app to relay back
to the browser, which then connects peer-to-peer to go2rtc.

This is a single-P2P-stream cut: no TURN/coturn, no adaptive framerate.
"""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Optional

import httpx
import yaml

from app.config import config
from app.integrations import Integration, register_integration
from app.registry import DeviceCategory
from app.schemas.config_schemas import CameraIntegrationConfig

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)

# How long to wait for go2rtc's HTTP API to come up before giving up.
READINESS_TIMEOUT_S = 10.0
READINESS_POLL_INTERVAL_S = 0.5
# Timeout for the SDP negotiation round-trip.
NEGOTIATE_TIMEOUT_S = 10.0
# How long to wait for go2rtc to exit on terminate before SIGKILL.
SHUTDOWN_TIMEOUT_S = 5.0
# go2rtc's default WebRTC UDP/TCP port; advertised as a host candidate.
WEBRTC_PORT = 8555


@register_integration
class CameraIntegration(Integration):
    """Integration that supervises go2rtc and brokers WebRTC signalling."""

    CONFIG_SCHEMA = CameraIntegrationConfig

    def __init__(self, config: dict[str, Any]):
        """Initialize the camera integration from validated config."""
        super().__init__(config)

        self.go2rtc_binary: str = self.config.get("go2rtc_binary", "go2rtc")
        self.go2rtc_host: str = self.config.get("go2rtc_host", "127.0.0.1")
        self.go2rtc_api_port: int = self.config.get("go2rtc_api_port", 1984)

        # Build the stream map (entity_id -> source) and the set of valid
        # stream ids up front from config — independent of register_capabilities
        # so negotiate_webrtc can reject unknown ids even before registration.
        self._streams: dict[str, str] = {}
        for cam in self.config.get("cameras", []) or []:
            if not isinstance(cam, dict):
                logger.error(f"Invalid camera config: {cam}")
                continue
            name = cam.get("name")
            source = cam.get("source")
            if not name or not source:
                logger.error(f"Invalid camera config (missing name/source): {cam}")
                continue
            self._streams[f"camera.{name}"] = source

        self._process: Optional[asyncio.subprocess.Process] = None
        self._config_path: Optional[str] = None

        logger.info(
            f"Camera Integration initialized with {len(self._streams)} camera(s); "
            f"go2rtc api {self.go2rtc_host}:{self.go2rtc_api_port}"
        )

    @property
    def _api_base(self) -> str:
        """Base URL for go2rtc's HTTP API."""
        return f"http://{self.go2rtc_host}:{self.go2rtc_api_port}"

    def _write_go2rtc_config(self) -> str:
        """Generate a go2rtc YAML config file and return its path.

        The config wires the HTTP API listener, a no-TURN WebRTC section that
        advertises host candidates on ``<host>:<WEBRTC_PORT>``, and the stream
        map keyed by each camera's entity_id.
        """
        data_dir = config.get("general.data_dir", "data")
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "go2rtc.yaml")

        go2rtc_config = {
            "api": {"listen": f":{self.go2rtc_api_port}"},
            "webrtc": {"candidates": [f"{self.go2rtc_host}:{WEBRTC_PORT}"]},
            "streams": dict(self._streams),
            # Keep go2rtc's own log noise modest; it inherits our stdio.
            "log": {"level": "warn"},
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(go2rtc_config, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Wrote go2rtc config to {path}")
        return path

    async def _wait_until_ready(self) -> bool:
        """Poll go2rtc's /api/streams until it responds (bounded)."""
        deadline = asyncio.get_event_loop().time() + READINESS_TIMEOUT_S
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                # If the process died early, stop waiting.
                if self._process is not None and self._process.returncode is not None:
                    logger.error(f"go2rtc exited early with code {self._process.returncode}")
                    return False
                try:
                    resp = await client.get(f"{self._api_base}/api/streams")
                    if resp.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(READINESS_POLL_INTERVAL_S)
        logger.error("go2rtc did not become ready within the readiness timeout")
        return False

    async def connect(self) -> bool:
        """Generate config, spawn go2rtc, and wait for its API to be ready.

        Returns False (without raising) on a missing binary or a failed
        readiness check, so other integrations still load.
        """
        if not self.config.get("enabled", False):
            logger.info("Camera Integration is disabled in configuration.")
            return False

        try:
            self._config_path = self._write_go2rtc_config()
        except Exception as e:
            logger.error(f"Failed to write go2rtc config: {e}")
            return False

        try:
            self._process = await asyncio.create_subprocess_exec(
                self.go2rtc_binary,
                "-config",
                self._config_path,
            )
        except FileNotFoundError:
            logger.error(
                f"go2rtc binary '{self.go2rtc_binary}' not found on PATH. "
                f"Camera streaming will be unavailable. Install go2rtc or set "
                f"integrations.camera.go2rtc_binary to its path."
            )
            return False
        except Exception as e:
            logger.error(f"Failed to start go2rtc: {e}")
            return False

        logger.info(f"Spawned go2rtc (pid={self._process.pid})")

        ready = await self._wait_until_ready()
        if not ready:
            await self.disconnect()
            return False

        logger.info("go2rtc is ready")
        return True

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register each configured camera as a CAMERA-category device."""
        for stream_id, _source in self._streams.items():
            # stream_id is "camera.<name>"; split off the name for register_device.
            name = stream_id.split(".", 1)[1]
            registry.register_device(
                name=name,
                domain="camera",
                device_type="camera",
                category=DeviceCategory.CAMERA,
                integration_name=self.name,
                metadata={"streamId": stream_id},
            )

    async def negotiate_webrtc(self, stream_id: str, offer_sdp: str) -> str:
        """Relay an SDP offer to go2rtc and return its answer SDP.

        Args:
            stream_id: The camera entity_id (``camera.<name>``). Must be one of
                this integration's configured cameras.
            offer_sdp: The browser's SDP offer.

        Returns:
            str: go2rtc's SDP answer (embeds its ICE candidates, non-trickle).

        Raises:
            ValueError: If ``stream_id`` is not a configured camera.
            RuntimeError: If go2rtc returns a non-200 or an answer without sdp.
        """
        if stream_id not in self._streams:
            raise ValueError(f"Unknown stream id: {stream_id}")

        url = f"{self._api_base}/api/webrtc"
        async with httpx.AsyncClient(timeout=NEGOTIATE_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                params={"src": stream_id},
                json={"type": "offer", "sdp": offer_sdp},
            )

        if resp.status_code != 200:
            raise RuntimeError(f"go2rtc webrtc negotiation failed: HTTP {resp.status_code}")

        try:
            answer = resp.json()
        except ValueError as e:
            raise RuntimeError("go2rtc returned a non-JSON answer") from e

        answer_sdp = answer.get("sdp")
        if not answer_sdp:
            raise RuntimeError("go2rtc answer is missing the sdp field")

        return answer_sdp

    async def receive_data(self) -> AsyncGenerator[dict[str, Any], None]:
        """Cameras emit no telemetry — empty async generator."""
        return
        yield {}  # pragma: no cover - makes this an async generator

    async def get_device_data(self) -> dict[str, Any]:
        """Cameras carry no state."""
        return {}

    async def send_data(self, data: dict[str, Any]) -> bool:
        """Cameras are not commanded via the data path — no-op success."""
        return True

    async def disconnect(self) -> None:
        """Terminate the supervised go2rtc process gracefully."""
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return

        try:
            process.terminate()
        except ProcessLookupError:
            return
        except Exception as e:
            logger.debug(f"Error terminating go2rtc: {e}")

        try:
            await asyncio.wait_for(process.wait(), timeout=SHUTDOWN_TIMEOUT_S)
            logger.info("go2rtc terminated")
        except asyncio.TimeoutError:
            logger.warning("go2rtc did not exit on terminate; killing")
            try:
                process.kill()
                await process.wait()
            except Exception as e:
                logger.debug(f"Error killing go2rtc: {e}")
