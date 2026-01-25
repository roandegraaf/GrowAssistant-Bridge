"""GrowAssistant Bridge - App Package."""

__version__ = "0.1.0"

try:
    from app.watchdog import watchdog_manager
except ImportError:
    watchdog_manager = None
