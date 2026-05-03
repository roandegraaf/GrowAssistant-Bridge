"""ESPHome Integration Package.

Native integration for ESPHome devices using the ESPHome API protocol
(https://esphome.io/components/api.html). Supports multiple devices per
integration, optional Noise encryption, auto-discovery of sensors, and
control of switches/lights/fans.
"""

from .esphome import ESPHomeIntegration

__all__ = ["ESPHomeIntegration"]
