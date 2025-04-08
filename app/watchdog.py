"""
Watchdog Module.

This module provides a watchdog that monitors the application and restarts it if it crashes.
"""

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, List, Callable

logger = logging.getLogger(__name__)

class WatchdogManager:
    """Watchdog manager that monitors the application and restarts it if it crashes.
    
    This watchdog starts a separate process that monitors the main application process.
    If the main process crashes, the watchdog will restart it.
    
    Attributes:
        _instance: Singleton instance of the WatchdogManager.
        _watchdog_process: The subprocess running the watchdog.
        _running: Whether the watchdog is running.
        _pid: The process ID of the main application.
        _restart_requested: Whether a restart has been requested.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Create or return the singleton instance.
        
        Returns:
            WatchdogManager: The singleton instance.
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(WatchdogManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        """Initialize the watchdog manager."""
        if self._initialized:
            return
            
        self._running = False
        self._watchdog_process: Optional[subprocess.Popen] = None
        self._restart_requested = False
        self._pid = os.getpid()  # Current process PID
        self._initialized = True
        self._exit_handlers: List[Callable] = []
        
        # Register an exit handler to clean up the watchdog
        atexit.register(self._cleanup_watchdog)
        
        logger.info("Watchdog manager initialized")
    
    def start(self):
        """Start the watchdog in a separate process."""
        if self._running:
            logger.warning("Watchdog already running")
            return
            
        logger.info(f"Starting watchdog to monitor PID {self._pid}")
        self._running = True
        
        # Create a simple Python script for the watchdog
        watchdog_script = self._create_watchdog_script()
        
        # Create environment for the subprocess
        env = os.environ.copy()
        
        # Start the watchdog process
        try:
            # Set creation flags based on platform
            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            
            self._watchdog_process = subprocess.Popen(
                [sys.executable, '-c', watchdog_script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                **kwargs
            )
            
            # Start a thread to read the watchdog output
            thread = threading.Thread(target=self._read_watchdog_output)
            thread.daemon = True
            thread.start()
            
            logger.info(f"Watchdog started with PID {self._watchdog_process.pid}")
        except Exception as e:
            logger.error(f"Failed to start watchdog: {e}")
            self._running = False
    
    def _create_watchdog_script(self) -> str:
        """Create a Python script for the watchdog process.
        
        Returns:
            str: The Python script as a string.
        """
        script_path = sys.argv[0]
        script_args = ' '.join(sys.argv[1:])
        python_executable = sys.executable
        main_pid = self._pid
        
        # This script will be executed in a separate process
        return f'''
import os
import sys
import time
import signal
import subprocess
import logging
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('watchdog')

# The main application's PID
main_pid = {main_pid}
script_path = r"{script_path}"
python_executable = r"{python_executable}"
script_args = "{script_args}"

print(f"Watchdog started to monitor PID {{main_pid}}")
logger.info(f"Watchdog will restart using: {{python_executable}} {{script_path}} {{script_args}}")

restart_process = None
running = True
deliberate_shutdown = False

def cleanup():
    global restart_process, running
    running = False
    if restart_process and restart_process.poll() is None:
        try:
            restart_process.terminate()
            restart_process.wait(timeout=5)
        except:
            if restart_process.poll() is None:
                restart_process.kill()
    logger.info("Watchdog exited")

atexit.register(cleanup)

# Handle signals
def handle_signal(sig, frame):
    global running, deliberate_shutdown
    logger.info(f"Watchdog received signal {{sig}}")
    deliberate_shutdown = True
    running = False
    sys.exit(0)

# Set up signal handlers if not on Windows or if on Windows with supported signals
if hasattr(signal, 'SIGINT'):
    signal.signal(signal.SIGINT, handle_signal)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, handle_signal)

def is_process_running(pid):
    try:
        # Windows-specific approach
        if sys.platform == 'win32':
            # Check using tasklist
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process != 0:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            # Unix-style approach
            os.kill(pid, 0)
            return True
    except OSError:
        return False
    except Exception as e:
        logger.error(f"Error checking if process is running: {{e}}")
        # Default to assuming it's running to prevent unnecessary restarts
        return True

def read_process_output(process):
    try:
        for line in process.stdout:
            sys.stdout.write(f"[RESTARTED] {{line}}")
            sys.stdout.flush()
    except Exception as e:
        logger.error(f"Error reading process output: {{e}}")

def create_managed_process():
    # Create environment
    env = os.environ.copy()
    env['WATCHDOG_MANAGED'] = '1'
    
    try:
        # Start the application
        cmd = [python_executable, script_path] + script_args.split()
        logger.info(f"Starting managed process: {{' '.join(cmd)}}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            env=env
        )
        
        # Start a thread to read the output
        import threading
        thread = threading.Thread(target=read_process_output, args=(process,))
        thread.daemon = True
        thread.start()
        
        logger.info(f"Process started with PID {{process.pid}}")
        return process
    except Exception as e:
        logger.error(f"Failed to start process: {{e}}")
        return None

# Monitor the main process
while running:
    # Check if the main process is still running
    if not is_process_running(main_pid):
        logger.info(f"Main process {{main_pid}} has exited")
        
        # Don't restart if it was a deliberate shutdown
        if deliberate_shutdown:
            logger.info("Deliberate shutdown detected, not restarting")
            break
            
        # Restart the application
        restart_process = create_managed_process()
        if restart_process:
            # Now monitor the restarted process instead
            main_pid = restart_process.pid
        else:
            # Wait before trying again
            time.sleep(5)
            
    # Check for restart request file
    if os.path.exists(".restart_requested"):
        logger.info("Restart requested via file")
        try:
            os.remove(".restart_requested")
        except:
            pass
            
        # Kill the current process
        try:
            if sys.platform == 'win32':
                # On Windows, we need to use taskkill
                subprocess.call(['taskkill', '/F', '/PID', str(main_pid)])
            else:
                # On Unix systems
                os.kill(main_pid, signal.SIGTERM)
            # Wait for it to exit
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error terminating process: {{e}}")
            
        # Restart the application
        restart_process = create_managed_process()
        if restart_process:
            main_pid = restart_process.pid
    
    # Wait a bit before checking again
    time.sleep(1)

logger.info("Watchdog exiting")
'''
    
    def _read_watchdog_output(self):
        """Read and log the watchdog process output."""
        if not self._watchdog_process or not self._watchdog_process.stdout:
            return
            
        try:
            for line in self._watchdog_process.stdout:
                # Log the line
                line = line.strip()
                if line:
                    logger.info(f"[Watchdog] {line}")
        except Exception as e:
            logger.error(f"Error reading watchdog output: {e}")
    
    def _cleanup_watchdog(self):
        """Clean up the watchdog when the main process exits."""
        if self._running and self._watchdog_process and self._watchdog_process.poll() is None:
            logger.info("Cleaning up watchdog process")
            try:
                self._watchdog_process.terminate()
                self._watchdog_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Watchdog did not terminate gracefully, killing it")
                self._watchdog_process.kill()
            self._running = False
    
    def stop(self, deliberate=True):
        """Stop the watchdog.
        
        Args:
            deliberate: Whether the shutdown was deliberate.
        """
        if not self._running:
            logger.warning("Watchdog not running")
            return
            
        logger.info(f"Stopping watchdog (deliberate={deliberate})")
        
        # Signal to the watchdog that this is a deliberate shutdown
        if deliberate and self._watchdog_process and self._watchdog_process.poll() is None:
            try:
                if sys.platform == 'win32':
                    # On Windows, we need to use CTRL_BREAK_EVENT
                    self._watchdog_process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    # On Unix, we can use SIGTERM
                    self._watchdog_process.terminate()
            except Exception as e:
                logger.error(f"Error sending signal to watchdog: {e}")
        
        self._cleanup_watchdog()
        self._running = False
        logger.info("Watchdog stopped")
    
    def request_restart(self):
        """Request a restart of the application."""
        if not self._running:
            logger.warning("Watchdog not running, can't request restart")
            return False
            
        logger.info("Requesting application restart via watchdog")
        
        # Create a file to signal the watchdog to restart the application
        try:
            with open(".restart_requested", "w") as f:
                f.write(f"{time.time()}")
            self._restart_requested = True
            return True
        except Exception as e:
            logger.error(f"Error creating restart request file: {e}")
            return False
    
    def register_exit_handler(self, handler: Callable):
        """Register a handler to be called when the application exits.
        
        Args:
            handler: The handler function to call.
        """
        self._exit_handlers.append(handler)

# Create a global instance
watchdog_manager = WatchdogManager() 