"""Bridge-side automations: receive/validate/report the rule set and run it.

``AutomationManager`` owns the rule set (receive, validate, status round-trip,
versioned reconciliation) and drives the ``AutomationEngine``, which evaluates
triggers/conditions and executes actions locally. The supporting primitives —
``StateStore`` (change-notifying entity values), ``EventBus`` (event triggers +
``fire_event``) and ``ActionExecutor`` (service→bridge-action translation) — are
re-exported for wiring in ``app/main.py`` and for tests.
"""

from .engine import AutomationEngine
from .event_bus import EventBus
from .executor import ActionExecutor
from .manager import AutomationManager
from .state_store import StateStore

__all__ = [
    "AutomationManager",
    "AutomationEngine",
    "StateStore",
    "EventBus",
    "ActionExecutor",
]
