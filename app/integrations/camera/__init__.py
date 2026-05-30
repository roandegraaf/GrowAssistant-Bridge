"""Camera Integration Package.

Brokers WebRTC streaming between the GrowAssistant app (over MQTT) and a
locally-supervised ``go2rtc`` process that acts as the actual WebRTC media
peer. The bridge merely relays SDP: it forwards the browser's offer to
go2rtc's HTTP API and returns go2rtc's answer (which embeds go2rtc's ICE
candidates — non-trickle) back to the app.
"""

from .camera import CameraIntegration

__all__ = ["CameraIntegration"]
