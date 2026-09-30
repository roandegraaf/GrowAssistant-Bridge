"""Per-entity sensor calibration: ``calibrated = raw * scale + offset``.

The app publishes calibrations inside the retained rule-set payload; the
collection loop applies them once, before a sample reaches the telemetry queue,
the automation StateStore and the ``on_telemetry`` fan-out. Pure functions.
"""

import math
from typing import Any, NamedTuple


class Calibration(NamedTuple):
    offset: float = 0.0
    scale: float = 1.0


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_calibrations(raw: Any) -> dict[str, Calibration]:
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, Calibration] = {}
    for entity_id, entry in raw.items():
        if not isinstance(entity_id, str) or "." not in entity_id or not isinstance(entry, dict):
            continue
        offset = entry.get("offset", 0.0)
        scale = entry.get("scale", 1.0)
        if not _finite(offset) or not _finite(scale) or scale <= 0:
            continue
        parsed[entity_id] = Calibration(float(offset), float(scale))
    return parsed


def calibrate(value: Any, calibration: Calibration | None) -> Any:
    """Apply a calibration to a numeric value (or numeric string); anything else passes through."""
    if calibration is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return value
    if not math.isfinite(number):
        return value
    return round(number * calibration.scale + calibration.offset, 6)
