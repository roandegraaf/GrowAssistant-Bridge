"""
Singleton Metaclass Utility.

This module provides a thread-safe singleton metaclass that eliminates
the need for repetitive singleton boilerplate code across the application.
"""

import threading
from typing import Any, Dict


class SingletonMeta(type):
    """Thread-safe singleton metaclass.

    This metaclass ensures only one instance of a class exists.
    It handles thread-safety and prevents re-initialization.

    Usage:
        class MyClass(metaclass=SingletonMeta):
            def __init__(self, arg1, arg2):
                # Initialization only happens once
                self.arg1 = arg1
                self.arg2 = arg2

        # First call creates and initializes
        instance1 = MyClass("a", "b")

        # Subsequent calls return the same instance without re-initializing
        instance2 = MyClass()  # Returns same instance as instance1

        assert instance1 is instance2  # True
    """

    _instances: Dict[type, Any] = {}
    _lock: threading.Lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        """Create or return the singleton instance.

        Thread-safe implementation using double-checked locking pattern.

        Args:
            *args: Arguments for class initialization (only used on first call).
            **kwargs: Keyword arguments for initialization (only used on first call).

        Returns:
            The singleton instance of the class.
        """
        # Fast path: instance already exists
        if cls not in cls._instances:
            # Slow path: need to create instance
            with cls._lock:
                # Double-check after acquiring lock
                if cls not in cls._instances:
                    instance = super().__call__(*args, **kwargs)
                    cls._instances[cls] = instance
        return cls._instances[cls]

    def reset_instance(cls) -> None:
        """Reset the singleton instance (useful for testing).

        This method removes the cached instance, allowing a new one
        to be created on the next instantiation.

        Warning:
            Use only for testing purposes. In production, singletons
            should remain constant throughout the application lifecycle.
        """
        with cls._lock:
            if cls in cls._instances:
                del cls._instances[cls]

    @property
    def instance(cls) -> Any:
        """Get the singleton instance without creating one.

        Returns:
            The singleton instance, or None if not yet created.
        """
        return cls._instances.get(cls)

    def is_initialized(cls) -> bool:
        """Check if the singleton has been initialized.

        Returns:
            True if an instance exists, False otherwise.
        """
        return cls in cls._instances
