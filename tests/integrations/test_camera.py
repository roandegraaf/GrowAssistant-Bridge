"""Tests for the CameraIntegration (go2rtc WebRTC broker).

go2rtc is fully mocked — no real binary, subprocess, or HTTP server is used.
httpx is patched via ``patch.object(httpx, "AsyncClient", ...)`` with an
AsyncMock.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations import ConfigurationError
from app.integrations.camera.camera import CameraIntegration
from app.registry import DeviceCategory, DeviceRegistry


def _config(**overrides):
    """Build a valid camera integration config dict."""
    base = {
        "enabled": True,
        "go2rtc_binary": "go2rtc",
        "go2rtc_api_port": 1984,
        "go2rtc_host": "127.0.0.1",
        "cameras": [{"name": "tent1", "source": "ffmpeg:test"}],
    }
    base.update(overrides)
    return base


@pytest.fixture
def registry():
    """Provide a fresh DeviceRegistry."""
    from app.utils.singleton import SingletonMeta

    if DeviceRegistry in SingletonMeta._instances:
        del SingletonMeta._instances[DeviceRegistry]
    reg = DeviceRegistry()
    yield reg
    reg.clear()


def _mock_async_client(response=None, post_response=None):
    """Build a MagicMock that behaves like httpx.AsyncClient as a ctx manager."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if response is not None:
        client.get = AsyncMock(return_value=response)
    if post_response is not None:
        client.post = AsyncMock(return_value=post_response)
    return client


class TestCameraConfig:
    """Configuration validation."""

    def test_valid_config(self):
        integration = CameraIntegration(_config())
        assert integration.go2rtc_host == "127.0.0.1"
        assert integration.go2rtc_api_port == 1984
        assert integration._streams == {"camera.tent1": "ffmpeg:test"}

    def test_defaults(self):
        integration = CameraIntegration({"enabled": True, "cameras": []})
        assert integration.go2rtc_binary == "go2rtc"
        assert integration.go2rtc_host == "127.0.0.1"
        assert integration.go2rtc_api_port == 1984
        assert integration._streams == {}

    def test_invalid_camera_missing_source(self):
        """A camera missing a required field fails schema validation."""
        with pytest.raises(ConfigurationError):
            CameraIntegration({"enabled": True, "cameras": [{"name": "tent1"}]})

    def test_invalid_port(self):
        with pytest.raises(ConfigurationError):
            CameraIntegration(_config(go2rtc_api_port=99999))

    def test_multiple_cameras(self):
        integration = CameraIntegration(
            _config(
                cameras=[
                    {"name": "tent1", "source": "ffmpeg:a"},
                    {"name": "tent2", "source": "rtsp://b"},
                ]
            )
        )
        assert integration._streams == {
            "camera.tent1": "ffmpeg:a",
            "camera.tent2": "rtsp://b",
        }


class TestRegisterCapabilities:
    """register_capabilities registers CAMERA-category devices."""

    def test_registers_camera_device(self, registry):
        integration = CameraIntegration(_config())
        integration.register_capabilities(registry)

        device = registry.get_device("camera.tent1")
        assert device is not None
        assert device.category == DeviceCategory.CAMERA
        assert device.domain == "camera"
        assert device.device_type == "camera"
        assert device.integration_name == "CameraIntegration"
        assert device.metadata["streamId"] == "camera.tent1"

    def test_camera_not_writable_in_manifest(self, registry):
        integration = CameraIntegration(_config())
        integration.register_capabilities(registry)

        manifest = registry.serialize_manifest(version=1)
        cam = next(d for d in manifest["devices"] if d["entityId"] == "camera.tent1")
        assert cam["entityDomain"] == "camera"
        assert cam["writable"] is False


class TestGo2rtcConfigGeneration:
    """The generated go2rtc YAML wires api/webrtc/streams correctly."""

    def test_write_config(self, tmp_path):
        integration = CameraIntegration(_config())
        with patch("app.integrations.camera.camera.config") as mock_config:
            mock_config.get.return_value = str(tmp_path)
            path = integration._write_go2rtc_config()

        import yaml

        with open(path) as f:
            written = yaml.safe_load(f)

        assert written["api"]["listen"] == ":1984"
        # Host candidate + a stun:<port> candidate for public-IP discovery.
        assert written["webrtc"]["candidates"] == ["127.0.0.1:8555", "stun:8555"]
        # No ICE servers fetched → no ice_servers key (host-only / STUN P2P).
        assert "ice_servers" not in written["webrtc"]
        # The base stream plus its reduced-framerate variant.
        assert written["streams"] == {
            "camera.tent1": "ffmpeg:test",
            "camera.tent1_lofps": "ffmpeg:camera.tent1#video=h264#raw=-vf fps=0.5",
        }

    def test_write_config_includes_ice_servers_when_present(self, tmp_path):
        integration = CameraIntegration(_config())
        integration._ice_servers = [
            {"urls": ["stun:stun.example.com:3478"]},
            {"urls": ["turn:turn.example.com:3478"], "username": "u", "credential": "c"},
        ]
        with patch("app.integrations.camera.camera.config") as mock_config:
            mock_config.get.return_value = str(tmp_path)
            path = integration._write_go2rtc_config()

        import yaml

        with open(path) as f:
            written = yaml.safe_load(f)

        assert written["webrtc"]["ice_servers"] == integration._ice_servers

    def test_low_framerate_fps_configurable(self, tmp_path):
        integration = CameraIntegration(_config(low_framerate_fps=1.5))
        with patch("app.integrations.camera.camera.config") as mock_config:
            mock_config.get.return_value = str(tmp_path)
            path = integration._write_go2rtc_config()

        import yaml

        with open(path) as f:
            written = yaml.safe_load(f)

        assert written["streams"]["camera.tent1_lofps"] == (
            "ffmpeg:camera.tent1#video=h264#raw=-vf fps=1.5"
        )


class TestConnect:
    """connect() spawns go2rtc and waits for readiness; resilient to failures."""

    @pytest.mark.asyncio
    async def test_disabled_returns_false(self):
        integration = CameraIntegration({"enabled": False, "cameras": []})
        assert await integration.connect() is False

    @pytest.mark.asyncio
    async def test_missing_binary_returns_false(self, tmp_path):
        integration = CameraIntegration(_config())
        with (
            patch("app.integrations.camera.camera.config") as mock_config,
            patch(
                "app.integrations.camera.camera.auth_manager.fetch_ice_servers",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.integrations.camera.camera.asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError(),
            ),
        ):
            mock_config.get.return_value = str(tmp_path)
            assert await integration.connect() is False

    @pytest.mark.asyncio
    async def test_connect_success(self, tmp_path):
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = None

        ready_response = MagicMock()
        ready_response.status_code = 200
        mock_client = _mock_async_client(response=ready_response)

        with (
            patch("app.integrations.camera.camera.config") as mock_config,
            patch(
                "app.integrations.camera.camera.auth_manager.fetch_ice_servers",
                AsyncMock(
                    return_value=[{"urls": ["turn:t:3478"], "username": "u", "credential": "c"}]
                ),
            ),
            patch(
                "app.integrations.camera.camera.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch.object(httpx, "AsyncClient", return_value=mock_client),
        ):
            mock_config.get.return_value = str(tmp_path)
            assert await integration.connect() is True
        assert integration._process is proc
        # ICE servers fetched from the app are stored for the go2rtc config.
        assert integration._ice_servers == [
            {"urls": ["turn:t:3478"], "username": "u", "credential": "c"}
        ]

    @pytest.mark.asyncio
    async def test_connect_success_without_ice_servers(self, tmp_path):
        """A failed ICE fetch doesn't block connect (host-only / STUN P2P)."""
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = None

        mock_client = _mock_async_client(response=MagicMock(status_code=200))

        with (
            patch("app.integrations.camera.camera.config") as mock_config,
            patch(
                "app.integrations.camera.camera.auth_manager.fetch_ice_servers",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.integrations.camera.camera.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch.object(httpx, "AsyncClient", return_value=mock_client),
        ):
            mock_config.get.return_value = str(tmp_path)
            assert await integration.connect() is True
        assert integration._ice_servers == []

    @pytest.mark.asyncio
    async def test_connect_process_dies_early(self, tmp_path):
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 1  # already exited
        proc.terminate = MagicMock()
        proc.wait = AsyncMock()

        mock_client = _mock_async_client(response=MagicMock(status_code=500))

        with (
            patch("app.integrations.camera.camera.config") as mock_config,
            patch(
                "app.integrations.camera.camera.auth_manager.fetch_ice_servers",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.integrations.camera.camera.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch.object(httpx, "AsyncClient", return_value=mock_client),
        ):
            mock_config.get.return_value = str(tmp_path)
            assert await integration.connect() is False


class TestNegotiateWebRTC:
    """negotiate_webrtc relays SDP to go2rtc and returns the answer."""

    @pytest.mark.asyncio
    async def test_success(self):
        integration = CameraIntegration(_config())
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json = MagicMock(return_value={"type": "answer", "sdp": "ANSWER_SDP"})
        mock_client = _mock_async_client(post_response=post_response)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            answer = await integration.negotiate_webrtc("camera.tent1", "OFFER_SDP")

        assert answer == "ANSWER_SDP"
        # Posted to the right URL with the right params/body.
        _, kwargs = mock_client.post.call_args
        args, _ = mock_client.post.call_args
        assert args[0] == "http://127.0.0.1:1984/api/webrtc"
        assert kwargs["params"] == {"src": "camera.tent1"}
        assert kwargs["json"] == {"type": "offer", "sdp": "OFFER_SDP"}

    @pytest.mark.asyncio
    async def test_low_framerate_variant_accepted(self):
        """The _lofps variant is a valid negotiation target (adaptive framerate)."""
        integration = CameraIntegration(_config())
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json = MagicMock(return_value={"type": "answer", "sdp": "ANSWER_SDP"})
        mock_client = _mock_async_client(post_response=post_response)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            answer = await integration.negotiate_webrtc("camera.tent1_lofps", "OFFER_SDP")

        assert answer == "ANSWER_SDP"
        _, kwargs = mock_client.post.call_args
        assert kwargs["params"] == {"src": "camera.tent1_lofps"}

    @pytest.mark.asyncio
    async def test_unknown_stream_rejected(self):
        integration = CameraIntegration(_config())
        with pytest.raises(ValueError, match="Unknown stream id"):
            await integration.negotiate_webrtc("camera.bogus", "OFFER_SDP")

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        integration = CameraIntegration(_config())
        post_response = MagicMock()
        post_response.status_code = 500
        mock_client = _mock_async_client(post_response=post_response)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="HTTP 500"):
                await integration.negotiate_webrtc("camera.tent1", "OFFER_SDP")

    @pytest.mark.asyncio
    async def test_missing_sdp_raises(self):
        integration = CameraIntegration(_config())
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json = MagicMock(return_value={"type": "answer"})
        mock_client = _mock_async_client(post_response=post_response)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="missing the sdp"):
                await integration.negotiate_webrtc("camera.tent1", "OFFER_SDP")


class TestDataPath:
    """Cameras emit no telemetry; get_device_data reports go2rtc stream status."""

    @pytest.mark.asyncio
    async def test_receive_data_yields_nothing(self):
        integration = CameraIntegration(_config())
        items = [item async for item in integration.receive_data()]
        assert items == []

    @pytest.mark.asyncio
    async def test_get_device_data_offline_without_go2rtc(self):
        """No supervised go2rtc process → every camera reports offline."""
        integration = CameraIntegration(_config())
        assert await integration.get_device_data() == {
            "tent1": {"type": "camera", "status": "offline", "value": "offline"}
        }

    @pytest.mark.asyncio
    async def test_get_device_data_streaming_from_go2rtc(self):
        """An active producer in go2rtc's /api/streams → status streaming."""
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.returncode = None
        integration._process = proc

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "camera.tent1": {
                "producers": [{"state": "active"}],
                "consumers": [{}],
            }
        }
        mock_client = _mock_async_client(response=response)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            data = await integration.get_device_data()

        assert data == {
            "tent1": {
                "type": "camera",
                "status": "streaming",
                "value": "streaming",
                "source": "ffmpeg:test",
                "consumers": 1,
            }
        }

    @pytest.mark.asyncio
    async def test_get_device_data_idle_when_no_producers(self):
        """Configured but nothing producing → status idle."""
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.returncode = None
        integration._process = proc

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"camera.tent1": {"producers": [], "consumers": []}}
        mock_client = _mock_async_client(response=response)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            data = await integration.get_device_data()

        assert data["tent1"]["status"] == "idle"

    @pytest.mark.asyncio
    async def test_get_device_data_offline_when_api_unreachable(self):
        """go2rtc process alive but API errors → offline (not a crash)."""
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.returncode = None
        integration._process = proc

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

        with patch.object(httpx, "AsyncClient", return_value=client):
            data = await integration.get_device_data()

        assert data["tent1"]["status"] == "offline"

    @pytest.mark.asyncio
    async def test_send_data_noop_true(self):
        integration = CameraIntegration(_config())
        assert await integration.send_data({"anything": 1}) is True


class TestDisconnect:
    """disconnect() terminates go2rtc, killing on timeout."""

    @pytest.mark.asyncio
    async def test_terminate_graceful(self):
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.wait = AsyncMock()
        integration._process = proc

        await integration.disconnect()

        proc.terminate.assert_called_once()
        assert integration._process is None

    @pytest.mark.asyncio
    async def test_kill_on_timeout(self):
        integration = CameraIntegration(_config())
        proc = MagicMock()
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        integration._process = proc

        # wait_for (the graceful wait) times out → kill() → final wait().
        async def fake_wait_for(coro, timeout):
            coro.close()  # avoid "never awaited" warning on the wait() coroutine
            raise TimeoutError()

        with patch(
            "app.integrations.camera.camera.asyncio.wait_for",
            side_effect=fake_wait_for,
        ):
            await integration.disconnect()

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_process_noop(self):
        integration = CameraIntegration(_config())
        integration._process = None
        await integration.disconnect()  # must not raise
