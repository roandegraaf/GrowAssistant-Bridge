"""
Tests for Watchdog Manager Module.

This module tests the WatchdogManager subprocess monitoring functionality
that monitors and restarts the application on crash.
"""

import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from app.watchdog import WatchdogManager


class TestWatchdogManagerInit:
    """Tests for WatchdogManager initialization."""

    def test_init_default_values(self):
        """Test that initialization sets correct default values."""
        manager = WatchdogManager()

        assert manager._running is False
        assert manager._watchdog_process is None
        assert manager._restart_requested is False
        assert manager._pid == os.getpid()
        assert manager._exit_handlers == []
        assert manager._deliberate_shutdown is False

    @patch("atexit.register")
    def test_init_registers_atexit_handler(self, mock_register):
        """Test that atexit handler is registered on init."""
        # Need to reset singleton to test fresh init
        from app.utils.singleton import SingletonMeta

        with SingletonMeta._lock:
            if WatchdogManager in SingletonMeta._instances:
                del SingletonMeta._instances[WatchdogManager]

        manager = WatchdogManager()

        mock_register.assert_called_once_with(manager._cleanup_watchdog)


class TestWatchdogStart:
    """Tests for WatchdogManager.start()."""

    @patch("subprocess.Popen")
    @patch("threading.Thread")
    def test_start_creates_subprocess(self, mock_thread, mock_popen):
        """Test that start creates a subprocess."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = MagicMock()
        mock_popen.return_value = mock_process

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        manager = WatchdogManager()
        manager.start()

        assert mock_popen.called
        assert manager._running is True
        assert manager._watchdog_process is mock_process

    @patch("subprocess.Popen")
    @patch("threading.Thread")
    def test_start_prevents_double_start(self, mock_thread, mock_popen):
        """Test that calling start twice doesn't create duplicate processes."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = MagicMock()
        mock_popen.return_value = mock_process

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        manager = WatchdogManager()
        manager.start()
        manager.start()  # Second call should be ignored

        assert mock_popen.call_count == 1

    @patch("subprocess.Popen")
    @patch("threading.Thread")
    def test_start_starts_output_reader_thread(self, mock_thread, mock_popen):
        """Test that start creates and starts the output reader thread."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = MagicMock()
        mock_popen.return_value = mock_process

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        manager = WatchdogManager()
        manager.start()

        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    @patch("subprocess.Popen")
    def test_start_handles_subprocess_failure(self, mock_popen):
        """Test that start handles subprocess creation failure."""
        mock_popen.side_effect = OSError("Failed to create process")

        manager = WatchdogManager()
        manager.start()

        assert manager._running is False
        assert manager._watchdog_process is None


class TestWatchdogStop:
    """Tests for WatchdogManager.stop()."""

    def test_stop_when_not_running(self):
        """Test stop when watchdog is not running."""
        manager = WatchdogManager()
        manager._running = False

        # Should not raise
        manager.stop()

    @patch("subprocess.Popen")
    def test_stop_terminates_process(self, mock_popen):
        """Test that stop terminates the watchdog process."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process still running
        mock_process.wait.return_value = 0

        manager = WatchdogManager()
        manager._running = True
        manager._watchdog_process = mock_process

        manager.stop(deliberate=True)

        mock_process.terminate.assert_called()
        assert manager._running is False

    def test_stop_with_deliberate_flag(self):
        """Test stop with deliberate shutdown flag."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.return_value = 0

        manager = WatchdogManager()
        manager._running = True
        manager._watchdog_process = mock_process

        manager.stop(deliberate=True)

        mock_process.terminate.assert_called()

    @patch("subprocess.Popen")
    def test_stop_handles_timeout(self, mock_popen):
        """Test that stop handles process termination timeout."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=5)

        manager = WatchdogManager()
        manager._running = True
        manager._watchdog_process = mock_process

        manager.stop(deliberate=True)

        mock_process.kill.assert_called()


class TestCreateWatchdogScript:
    """Tests for WatchdogManager._create_watchdog_script()."""

    def test_script_contains_main_pid(self):
        """Test that generated script contains the main PID."""
        manager = WatchdogManager()
        script = manager._create_watchdog_script()

        assert f"main_pid = {manager._pid}" in script

    def test_script_contains_python_executable(self):
        """Test that generated script contains the Python executable path."""
        manager = WatchdogManager()
        script = manager._create_watchdog_script()

        assert sys.executable in script

    def test_script_contains_signal_handlers(self):
        """Test that generated script contains signal handler setup."""
        manager = WatchdogManager()
        script = manager._create_watchdog_script()

        assert "signal.signal" in script
        assert "handle_signal" in script

    def test_script_contains_process_monitoring(self):
        """Test that generated script contains process monitoring logic."""
        manager = WatchdogManager()
        script = manager._create_watchdog_script()

        assert "is_process_running" in script
        assert "while running:" in script

    def test_script_contains_restart_logic(self):
        """Test that generated script contains restart logic."""
        manager = WatchdogManager()
        script = manager._create_watchdog_script()

        assert "create_managed_process" in script
        assert ".restart_requested" in script


class TestRequestRestart:
    """Tests for WatchdogManager.request_restart()."""

    def test_request_creates_restart_file(self, tmp_path):
        """Test that request_restart creates the restart file."""
        # Change to temp directory for file operations
        original_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            manager = WatchdogManager()
            manager._running = True

            result = manager.request_restart()

            assert result is True
            assert manager._restart_requested is True
            assert (tmp_path / ".restart_requested").exists()
        finally:
            os.chdir(original_cwd)
            # Clean up
            restart_file = tmp_path / ".restart_requested"
            if restart_file.exists():
                restart_file.unlink()

    def test_request_when_not_running(self):
        """Test request_restart when watchdog is not running."""
        manager = WatchdogManager()
        manager._running = False

        result = manager.request_restart()

        assert result is False

    @patch("builtins.open")
    def test_request_handles_file_error(self, mock_open):
        """Test that request_restart handles file creation errors."""
        mock_open.side_effect = OSError("Permission denied")

        manager = WatchdogManager()
        manager._running = True

        result = manager.request_restart()

        assert result is False


class TestCleanupWatchdog:
    """Tests for WatchdogManager._cleanup_watchdog()."""

    def test_cleanup_terminates_process(self):
        """Test that cleanup terminates the watchdog process."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process still running
        mock_process.wait.return_value = 0

        manager = WatchdogManager()
        manager._running = True
        manager._watchdog_process = mock_process

        manager._cleanup_watchdog()

        mock_process.terminate.assert_called()
        mock_process.wait.assert_called_once_with(timeout=5)
        assert manager._running is False

    def test_cleanup_kills_on_timeout(self):
        """Test that cleanup kills process on timeout."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=5)

        manager = WatchdogManager()
        manager._running = True
        manager._watchdog_process = mock_process

        manager._cleanup_watchdog()

        mock_process.kill.assert_called()

    def test_cleanup_when_not_running(self):
        """Test cleanup when watchdog is not running."""
        manager = WatchdogManager()
        manager._running = False

        # Should not raise
        manager._cleanup_watchdog()

    def test_cleanup_when_process_already_exited(self):
        """Test cleanup when process has already exited."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Process already exited

        manager = WatchdogManager()
        manager._running = True
        manager._watchdog_process = mock_process

        manager._cleanup_watchdog()

        # terminate should not be called if process already exited
        mock_process.terminate.assert_not_called()


class TestSetDeliberateShutdown:
    """Tests for WatchdogManager.set_deliberate_shutdown()."""

    def test_sets_flag_true(self):
        """Test setting deliberate shutdown flag to True."""
        manager = WatchdogManager()

        manager.set_deliberate_shutdown(True)

        assert manager._deliberate_shutdown is True

    def test_sets_flag_false(self):
        """Test setting deliberate shutdown flag to False."""
        manager = WatchdogManager()
        manager._deliberate_shutdown = True

        manager.set_deliberate_shutdown(False)

        assert manager._deliberate_shutdown is False


class TestRegisterExitHandler:
    """Tests for WatchdogManager.register_exit_handler()."""

    def test_register_handler(self):
        """Test registering an exit handler."""
        manager = WatchdogManager()
        handler = MagicMock()

        manager.register_exit_handler(handler)

        assert handler in manager._exit_handlers

    def test_register_multiple_handlers(self):
        """Test registering multiple exit handlers."""
        manager = WatchdogManager()
        handler1 = MagicMock()
        handler2 = MagicMock()

        manager.register_exit_handler(handler1)
        manager.register_exit_handler(handler2)

        assert len(manager._exit_handlers) == 2
        assert handler1 in manager._exit_handlers
        assert handler2 in manager._exit_handlers


class TestReadWatchdogOutput:
    """Tests for WatchdogManager._read_watchdog_output()."""

    def test_reads_output_lines(self):
        """Test reading output lines from watchdog process."""
        mock_process = MagicMock()
        mock_process.stdout = iter(["line1\n", "line2\n", "line3\n"])

        manager = WatchdogManager()
        manager._watchdog_process = mock_process

        # This should complete without error
        manager._read_watchdog_output()

    def test_handles_no_process(self):
        """Test handling when no process is available."""
        manager = WatchdogManager()
        manager._watchdog_process = None

        # Should not raise
        manager._read_watchdog_output()

    def test_handles_no_stdout(self):
        """Test handling when process has no stdout."""
        mock_process = MagicMock()
        mock_process.stdout = None

        manager = WatchdogManager()
        manager._watchdog_process = mock_process

        # Should not raise
        manager._read_watchdog_output()

    def test_handles_read_exception(self):
        """Test handling exceptions during output reading."""
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.__iter__ = MagicMock(side_effect=OSError("Read error"))

        manager = WatchdogManager()
        manager._watchdog_process = mock_process

        # Should not raise
        manager._read_watchdog_output()


class TestSingleton:
    """Tests for WatchdogManager singleton behavior."""

    def test_singleton_returns_same_instance(self):
        """Test that WatchdogManager returns the same instance."""
        from app.utils.singleton import SingletonMeta

        # Reset singleton
        with SingletonMeta._lock:
            if WatchdogManager in SingletonMeta._instances:
                del SingletonMeta._instances[WatchdogManager]

        manager1 = WatchdogManager()
        manager2 = WatchdogManager()

        assert manager1 is manager2
