"""
GrowAssistant Bridge - App Package.

This package contains the core application logic for the GrowAssistant Bridge.
"""

__version__ = '0.1.0'

# Import the watchdog manager for convenience
try:
    from app.watchdog import watchdog_manager
except ImportError:
    # This will happen during initial import before watchdog.py exists
    watchdog_manager = None 