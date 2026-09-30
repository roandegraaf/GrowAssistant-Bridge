"""Derived grow metrics (VPD, dew point, DLI) for ``derived`` conditions.

The formulas must stay identical to the app's TypeScript implementation so a
flow evaluates the same on both sides.
"""

import math
from datetime import date, datetime, time
from typing import Any, Optional

MAGNUS_A = 17.27
MAGNUS_B = 237.3
DLI_MAX_GAP_SECONDS = 30 * 60


def to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def svp_kpa(temp_c: float) -> float:
    return 0.6108 * math.exp(MAGNUS_A * temp_c / (temp_c + MAGNUS_B))


def vpd_kpa(temp_c: float, rh: float, leaf_offset: float = -2.0) -> float:
    return svp_kpa(temp_c + leaf_offset) - svp_kpa(temp_c) * rh / 100


def dew_point_c(temp_c: float, rh: float) -> Optional[float]:
    if rh <= 0:
        return None
    gamma = math.log(rh / 100) + MAGNUS_A * temp_c / (MAGNUS_B + temp_c)
    return MAGNUS_B * gamma / (MAGNUS_A - gamma)


# ponytail: in-memory, resets on bridge restart; persist if DLI conditions need to survive restarts
class DliAccumulator:
    """Integrates PPFD (µmol/m²/s) over the local day into mol/m²/day.

    Each sample's value is held until the next one (left rectangle); a hold
    longer than ``DLI_MAX_GAP_SECONDS`` contributes nothing, so a sensor that
    went silent doesn't keep "shining".
    """

    def __init__(self) -> None:
        self._day: Optional[date] = None
        self._total = 0.0
        self._last: Optional[tuple[datetime, float]] = None

    def _hold(self, until: datetime) -> float:
        if self._last is None:
            return 0.0
        at, ppfd = self._last
        gap = (until - at).total_seconds()
        if gap <= 0 or gap > DLI_MAX_GAP_SECONDS:
            return 0.0
        start = max(at, datetime.combine(until.date(), time.min, tzinfo=until.tzinfo))
        return ppfd * (until - start).total_seconds()

    def add(self, value: Any, now: datetime) -> None:
        ppfd = to_float(value)
        if ppfd is None:
            return
        if now.date() != self._day:
            self._day = now.date()
            self._total = 0.0
        self._total += self._hold(now)
        self._last = (now, ppfd)

    def value(self, now: datetime) -> Optional[float]:
        if self._last is None:
            return None
        total = self._total if now.date() == self._day else 0.0
        return (total + self._hold(now)) / 1e6
