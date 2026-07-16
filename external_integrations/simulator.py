"""
Simulated grow-tent sensor integration.

Produces realistic, smoothly-varying readings for a small indoor grow tent so
a development machine with no physical sensors still exercises the full
pipeline (registry → manifest → telemetry → app ingest). Values follow a
diurnal light schedule with per-sensor noise, so charts in the app look like
a real tent rather than white noise.

Configuration:
```yaml
integrations:
  simulator:
    enabled: true
    lights_on_hour: 6    # lights-on hour of day (default 6)
    lights_off_hour: 24  # lights-off hour of day (default 24 = midnight)
```
"""

import logging
import math
import random
import time
from collections.abc import Generator
from typing import Any

from app.integrations import Integration, register_integration
from app.registry import DeviceCategory

logger = logging.getLogger(__name__)

# name -> (device_type, unit, description of the curve)
SENSORS = {
    "tent_temperature": ("temperature", "°C"),
    "tent_humidity": ("humidity", "%"),
    "soil_moisture": ("water_level", "%"),
    "co2": ("pressure", "ppm"),
    "water_tank_level": ("water_level", "%"),
}


@register_integration
class SimulatorIntegration(Integration):
    """Fake sensors for development machines without hardware."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.lights_on_hour = float(self.config.get("lights_on_hour", 6))
        self.lights_off_hour = float(self.config.get("lights_off_hour", 24))
        # Per-sensor random-walk offsets so consecutive readings stay smooth.
        self._walk = {name: 0.0 for name in SENSORS}
        # Watering/tank state: soil drains until a "watering" refills it and
        # takes a matching gulp out of the tank.
        self._soil = 62.0
        self._tank = 88.0
        self._last_tick = time.time()

    async def connect(self) -> bool:
        logger.info(
            "Simulator connected (lights %02.0f:00-%02.0f:00)",
            self.lights_on_hour,
            self.lights_off_hour % 24,
        )
        return True

    async def disconnect(self):
        return None

    async def send_data(self, data: dict[str, Any]) -> bool:
        # Sensors only — nothing to actuate.
        return False

    def register_capabilities(self, registry) -> None:
        for name, (device_type, unit) in SENSORS.items():
            registry.register_device(
                name=name,
                domain=registry._derive_domain(self.name),
                device_type=device_type,
                category=DeviceCategory.SENSOR,
                integration_name=self.name,
                metadata={"unit": unit},
            )
        logger.info("Registered %d simulated sensors", len(SENSORS))

    # ─── Simulation ─────────────────────────────────────────────────

    def _lights_on(self, now: float) -> bool:
        hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60
        on, off = self.lights_on_hour, self.lights_off_hour % 24
        if on == off:
            return True
        if on < off:
            return on <= hour < off
        return hour >= on or hour < off

    def _drift(self, name: str, scale: float) -> float:
        """Bounded random walk so readings wander without jumping."""
        self._walk[name] = max(-2 * scale, min(2 * scale, self._walk[name] + random.uniform(-scale, scale)))
        return self._walk[name]

    def _readings(self) -> dict[str, float]:
        now = time.time()
        dt_min = max(0.0, (now - self._last_tick) / 60)
        self._last_tick = now

        t = time.localtime(now)
        day_frac = (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec) / 86400
        # Diurnal wave peaking mid-photoperiod.
        wave = math.sin(2 * math.pi * (day_frac - 0.33))
        lights = self._lights_on(now)

        temp = (24.5 if lights else 20.5) + 1.5 * wave + self._drift("tent_temperature", 0.05)
        hum = (58 if lights else 66) - 3 * wave + self._drift("tent_humidity", 0.15)
        co2 = (650 if lights else 950) + 60 * wave + self._drift("co2", 4.0)

        # Soil dries ~6%/hour under lights, ~2%/hour dark; auto-water at 35%.
        self._soil -= (0.10 if lights else 0.033) * dt_min
        if self._soil <= 35.0 and self._tank > 8.0:
            logger.info("Simulated watering event (soil %.1f%%)", self._soil)
            self._soil = 62.0 + random.uniform(-1.5, 1.5)
            self._tank -= 6.0
        if self._tank <= 8.0:  # someone "refills" the reservoir
            self._tank = 90.0 + random.uniform(-2.0, 2.0)
        soil = self._soil + self._drift("soil_moisture", 0.08)

        return {
            "tent_temperature": round(temp, 1),
            "tent_humidity": round(max(30.0, min(85.0, hum)), 1),
            "soil_moisture": round(max(5.0, min(80.0, soil)), 1),
            "co2": round(max(400.0, co2)),
            "water_tank_level": round(max(0.0, self._tank), 1),
        }

    async def receive_data(self) -> Generator[dict[str, Any], None, None]:
        for name, value in self._readings().items():
            yield {"device": name, "value": value, "type": SENSORS[name][0]}

    async def get_device_data(self) -> dict[str, Any]:
        readings = self._readings()
        return {
            name: {"type": SENSORS[name][0], "value": value, "unit": SENSORS[name][1]}
            for name, value in readings.items()
        }
