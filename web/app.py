"""
Web Application Module.

This module provides a Flask web interface for the GrowAssistant Bridge.
"""

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from concurrent.futures import wait
from functools import wraps
from pathlib import Path

import yaml
from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth import auth_manager
from app.config import config
from app.registry import registry

# Constant for masked sensitive values
MASKED_VALUE = "**********"

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
app = Flask(
    __name__,
    template_folder=str(WEB_DIR / "templates"),
    static_folder=str(WEB_DIR / "static"),
)


# Security middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses."""
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com;"
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:;"
    )
    return response


def login_required(f):
    """Decorator to require login for routes."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_enabled = config.get("web.auth_enabled", False)
        is_logged_in = session.get("logged_in")

        if auth_enabled and not is_logged_in:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


def is_password_set() -> bool:
    """Check if a password has been set in the configuration.

    Returns True if a password hash exists and differs from the default "admin" hash.
    """
    password_hash = config.get("web.password_hash", "")
    if not password_hash:
        return False
    default_hash = generate_password_hash("admin")
    return password_hash != default_hash


@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors."""
    return render_template("error.html", error="Page not found", code=404), 404


@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 errors."""
    logger.error("Internal server error: %s", e)
    return render_template("error.html", error="Internal server error", code=500), 500


def _validate_setup_form(password: str | None, confirm_password: str | None) -> str | None:
    """Validate password setup form fields.

    Returns an error message if validation fails, None otherwise.
    """
    if not password:
        return "Password is required"
    if len(password) < 8:
        return "Password must be at least 8 characters long"
    if password != confirm_password:
        return "Passwords do not match"
    return None


def _save_password_to_config(username: str, password_hash: str) -> None:
    """Save username and password hash to configuration file."""
    with open(config.config_file) as f:
        config_data = yaml.safe_load(f)

    config_data.setdefault("web", {})
    config_data["web"]["username"] = username
    config_data["web"]["password_hash"] = password_hash

    with open(config.config_file, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

    config.reload()


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First-time setup page to set password."""
    if is_password_set():
        return redirect(url_for("login"))

    error = None

    if request.method == "POST":
        username = request.form.get("username", "admin")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        error = _validate_setup_form(password, confirm_password)
        if error is None:
            try:
                password_hash = generate_password_hash(password)
                _save_password_to_config(username, password_hash)
                return redirect(url_for("login"))
            except Exception as e:
                logger.error("Error setting password: %s", e)
                error = f"Error setting password: {e}"

    return render_template("setup.html", error=error)


def _verify_credentials(username: str, password: str) -> bool:
    """Verify username and password against stored credentials."""
    stored_username = config.get("web.username", "admin")
    stored_password_hash = config.get("web.password_hash", "")
    return username == stored_username and check_password_hash(stored_password_hash, password)


def _log_failed_login(username: str) -> None:
    """Log a failed login attempt with IP and timestamp."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    logger.warning(
        "Failed login attempt - IP: %s, Username: %s, Time: %s",
        request.remote_addr,
        username,
        timestamp,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    if not config.get("web.auth_enabled", False):
        session["logged_in"] = True
        return redirect(url_for("index"))

    if not is_password_set():
        return redirect(url_for("setup"))

    error = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if _verify_credentials(username, password):
            session.clear()
            session["logged_in"] = True
            next_url = request.args.get("next", url_for("index"))
            return redirect(next_url)

        error = "Invalid username or password"
        _log_failed_login(username)

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """Logout."""
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    """Render dashboard only if connected and ready."""
    if not auth_manager.is_ready_for_data():
        return redirect(url_for("onboarding"))
    return render_template("index.html")


@app.route("/onboarding")
@login_required
def onboarding():
    """Render the onboarding page for device registration."""
    if auth_manager.is_ready_for_data():
        return redirect(url_for("index"))

    auth_code = auth_manager.get_auth_code()
    connection_timed_out = auth_manager.is_connection_timed_out()
    is_authenticated = auth_manager.is_authenticated()

    return render_template(
        "onboarding.html",
        auth_code=auth_code,
        connection_timed_out=connection_timed_out,
        is_authenticated=is_authenticated,
    )


@app.route("/", methods=["POST"])
@login_required
def handle_root_post():
    """Handle unexpected POST requests to the root URL."""
    logger.warning("Received unexpected POST request to root URL: %s", request.data)
    return jsonify({"error": "POST method not supported for this endpoint"}), 405


@app.route("/config")
@login_required
def config_page():
    """Render the configuration page."""
    return render_template("config.html")


@app.route("/api/device-types", methods=["GET"])
@login_required
def get_device_types():
    """Get all registered device types."""
    try:
        device_types = registry.get_device_types()
        result = {
            device_type: registry.get_device_actions(device_type) for device_type in device_types
        }
        logger.info("Device types response: %s", result)
        return jsonify(result)
    except Exception as e:
        logger.error("Error getting device types: %s", e)
        return jsonify({"error": str(e)}), 500


def _get_app_instance(endpoint_name: str):
    """Get the application instance from Flask context.

    Returns (app_instance, error_response) tuple. If error_response is not None,
    return it directly from the calling endpoint.
    """
    app_instance = current_app.config.get("APPLICATION_INSTANCE")

    if app_instance is None:
        logger.warning("Application instance not found in Flask context for %s", endpoint_name)
        return None, (jsonify({"error": "Application instance not available yet."}), 503)

    if not hasattr(app_instance, "_integrations"):
        logger.error("Application instance has no _integrations attribute")
        return None, (jsonify({"error": "Integrations not found in application."}), 500)

    return app_instance, None


def _get_enabled_integrations() -> list[str]:
    """Get list of integration names that are enabled in config."""
    integrations_config = config.get_section("integrations")
    return [name for name, cfg in integrations_config.items() if cfg.get("enabled", False)]


@app.route("/api/integrations", methods=["GET"])
@login_required
def get_integrations():
    """Get information about loaded integrations."""
    try:
        app_instance, error_response = _get_app_instance("/api/integrations")
        if error_response:
            return error_response

        if not app_instance._integrations:
            enabled = _get_enabled_integrations()
            if enabled:
                logger.info("Integrations defined in config but not loaded yet: %s", enabled)
                return jsonify({"message": "Integrations are still loading."}), 202
            logger.info("No integrations are enabled in the configuration.")
            return jsonify([])

        integrations = []
        for name, integration in app_instance._integrations.items():
            try:
                integrations.append(
                    {
                        "name": name,
                        "type": integration.__class__.__name__,
                        "status": "active",
                    }
                )
                logger.debug("Found integration: %s (%s)", name, integration.__class__.__name__)
            except Exception as e:
                logger.error("Error processing integration %s: %s", name, e)

        logger.info("Returning %d integrations", len(integrations))
        return jsonify(integrations)
    except Exception as e:
        logger.exception("Error getting integrations: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/queue", methods=["GET"])
@login_required
def get_queue_info():
    """Get information about the queue."""
    try:
        from app.queue_manager import queue_manager

        return jsonify(
            {
                "size": queue_manager.size(),
                "empty": queue_manager.is_empty(),
            }
        )
    except Exception as e:
        logger.error("Error getting queue info: %s", e)
        return jsonify({"error": str(e)}), 500


def _collect_device_data_from_integrations(app_instance) -> dict:
    """Collect device data from all integrations asynchronously.

    Schedules coroutines on the main event loop and waits for results with timeout.
    """
    if not app_instance.loop:
        raise RuntimeError("Event loop missing")

    futures_by_name = {
        name: asyncio.run_coroutine_threadsafe(integration.get_device_data(), app_instance.loop)
        for name, integration in app_instance._integrations.items()
    }

    done, not_done = wait(list(futures_by_name.values()), timeout=10.0)

    all_device_data = {}

    for name, future in futures_by_name.items():
        if future in not_done:
            all_device_data[name] = {"error": "Timeout getting data"}
            continue

        try:
            result = future.result()
            if isinstance(result, dict):
                for device_name, device_info in result.items():
                    all_device_data[f"{name}.{device_name}"] = device_info
            else:
                logger.warning(
                    "Unexpected data type from %s.get_device_data: %s", name, type(result)
                )
        except Exception as e:
            logger.error("Error getting result from integration %s: %s", name, e)
            all_device_data[name] = {"error": str(e)}

    if not_done:
        logger.warning("Timeout waiting for device data from %d integrations", len(not_done))

    return all_device_data


@app.route("/api/devices", methods=["GET"])
@login_required
def get_devices():
    """Get the current data/state for all registered devices across integrations."""
    try:
        app_instance, error_response = _get_app_instance("/api/devices")
        if error_response:
            return error_response

        if not app_instance._integrations:
            return jsonify({})

        if not app_instance.loop:
            logger.error("Main application event loop not found!")
            return jsonify({"error": "Internal server error: Event loop missing."}), 500

        device_data = _collect_device_data_from_integrations(app_instance)
        return jsonify(device_data)
    except Exception as e:
        logger.exception("Error getting device data: %s", e)
        return jsonify({"error": "Internal server error"}), 500


def _mask_sensitive_config_data(config_data: dict) -> dict:
    """Mask sensitive values in configuration data for safe display."""
    if "api" in config_data and config_data["api"].get("auth_token"):
        config_data["api"]["auth_token"] = MASKED_VALUE

    if "web" in config_data:
        for key in ("password_hash", "secret_key"):
            if key in config_data["web"]:
                config_data["web"][key] = MASKED_VALUE

    mqtt_config = config_data.get("integrations", {}).get("mqtt", {})
    if mqtt_config.get("password"):
        mqtt_config["password"] = MASKED_VALUE

    return config_data


@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    """Get the current configuration."""
    try:
        if request.args.get("format") == "raw":
            with open(config.config_file) as f:
                return Response(f.read(), mimetype="text/plain")

        with open(config.config_file) as f:
            config_data = yaml.safe_load(f)

        config_data = _mask_sensitive_config_data(config_data)
        return jsonify(config_data)
    except Exception as e:
        logger.error("Error reading configuration: %s", e)
        return jsonify({"error": str(e)}), 500


def _restore_masked_sensitive_values(config_data: dict, current_config: dict) -> dict:
    """Restore masked sensitive values from current configuration."""
    # API auth token
    if config_data.get("api", {}).get("auth_token") == MASKED_VALUE:
        config_data["api"]["auth_token"] = current_config.get("api", {}).get("auth_token", "")

    # Web section sensitive fields
    web_data = config_data.get("web", {})
    current_web = current_config.get("web", {})
    for key in ("password_hash", "secret_key"):
        if web_data.get(key) == MASKED_VALUE:
            web_data[key] = current_web.get(key, "")

    # MQTT password
    mqtt_data = config_data.get("integrations", {}).get("mqtt", {})
    if mqtt_data.get("password") == MASKED_VALUE:
        current_mqtt = current_config.get("integrations", {}).get("mqtt", {})
        mqtt_data["password"] = current_mqtt.get("password", "")

    return config_data


def _backup_config_file() -> None:
    """Create a backup of the current configuration file."""
    backup_file = f"{config.config_file}.bak"
    with open(config.config_file) as src, open(backup_file, "w") as dst:
        dst.write(src.read())


@app.route("/api/config", methods=["POST"])
@login_required
def update_config():
    """Update the configuration."""
    try:
        config_data = request.json
        if not config_data:
            return jsonify({"error": "No data provided"}), 400

        with open(config.config_file) as f:
            current_config = yaml.safe_load(f)

        config_data = _restore_masked_sensitive_values(config_data, current_config)

        try:
            _backup_config_file()
        except Exception as e:
            logger.error("Error creating backup: %s", e)
            return jsonify({"error": f"Failed to create backup: {e}"}), 500

        try:
            with open(config.config_file, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error("Error writing configuration: %s", e)
            return jsonify({"error": f"Failed to write configuration: {e}"}), 500

        return jsonify(
            {
                "success": True,
                "message": "Configuration updated. Restart the application for changes to take effect.",
            }
        )
    except Exception as e:
        logger.error("Error updating configuration: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/send-command", methods=["POST"])
@login_required
def send_command():
    """Send a command to a device."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        action = data.get("action")
        target = data.get("target")

        if not action or not target:
            return jsonify({"error": "Missing action or target"}), 400

        action_key = f"{action}_{target}"
        if not registry.has_integration_for_action(action_key):
            return jsonify({"error": f"No integration found for action: {action_key}"}), 404

        from app.api_client import api_client

        command = {
            "id": f"web-{int(time.time())}",
            "action": action,
            "target": target,
            "payload": data.get("payload", {}),
        }

        asyncio.create_task(api_client._command_queue.put(command))
        return jsonify({"success": True, "message": "Command sent for processing"})
    except Exception as e:
        logger.error("Error sending command: %s", e)
        return jsonify({"error": str(e)}), 500


def _perform_restart(app_instance) -> None:
    """Execute the restart process in a background thread."""
    logger.info("Initiating application restart...")
    time.sleep(2)  # Allow response to be sent

    # Try watchdog restart first
    try:
        from app.watchdog import watchdog_manager

        logger.info("Requesting restart via watchdog")
        if watchdog_manager.request_restart():
            return
    except ImportError:
        logger.warning("Watchdog not available, using fallback restart method")

    # Fallback: graceful shutdown with exit code 42
    logger.info("Using fallback restart method (exit code 42)")
    if app_instance.loop:
        asyncio.run_coroutine_threadsafe(app_instance.stop(), app_instance.loop)
        time.sleep(2)
        logger.info("Exiting with code 42 to trigger restart")
        os._exit(42)


@app.route("/api/restart", methods=["POST"])
@login_required
def restart_server():
    """Restart the application server.

    Initiates a graceful shutdown and restart. Client should expect brief disconnection.
    """
    try:
        app_instance = current_app.config.get("APPLICATION_INSTANCE")
        if app_instance is None:
            logger.warning("Application instance not found for restart request")
            return jsonify({"error": "Application instance not available."}), 503

        restart_thread = threading.Thread(
            target=_perform_restart,
            args=(app_instance,),
            daemon=True,
        )
        restart_thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Server restart initiated. The server will be unavailable briefly.",
            }
        )
    except Exception as e:
        logger.error("Error restarting server: %s", e)
        return jsonify({"error": str(e)}), 500


def _build_status_response(
    authenticated: bool = False,
    connected: bool = False,
    status: str = "initializing",
    ready: bool = False,
    api_initialized: bool = False,
    **extra,
) -> dict:
    """Build a standardized connection status response."""
    return {
        "authenticated": authenticated,
        "connected": connected,
        "status": status,
        "ready": ready,
        "api_client_initialized": api_initialized,
        **extra,
    }


async def _check_auth_status(api_initialized: bool) -> dict:
    """Check authentication and connection status asynchronously."""
    if not hasattr(auth_manager, "_client") or auth_manager._client is None:
        return _build_status_response(
            status="initializing",
            message="Authentication manager is initializing",
            api_initialized=api_initialized,
        )

    auth_code = auth_manager.get_auth_code()
    connection_timed_out = auth_manager.is_connection_timed_out()

    # Check if we timed out waiting for connection (before checking authenticated status)
    # This takes priority because even with credentials, the connection may have timed out
    if connection_timed_out:
        return _build_status_response(
            status="connection_timeout",
            message="Connection polling timed out. Click 'Get New Code' to try again.",
            api_initialized=api_initialized,
        )

    # If we have an auth code, we're still in registration phase waiting for the user
    # to enter the code - regardless of whether credentials are saved
    if auth_code:
        return _build_status_response(
            status="registration",
            auth_code=auth_code,
            api_initialized=api_initialized,
        )

    if not auth_manager.is_authenticated():
        return _build_status_response(
            status="not_registered",
            api_initialized=api_initialized,
        )

    connected, status = await auth_manager.check_connection_status()
    return _build_status_response(
        authenticated=True,
        connected=connected,
        status=status,
        ready=auth_manager.is_ready_for_data(),
        api_initialized=api_initialized,
        client_id=auth_manager.get_client_id(),
    )


@app.route("/api/connection-status", methods=["GET"])
@login_required
def get_connection_status():
    """Get the current connection status of the application."""
    try:
        from app.api_client import api_client

        api_init_state = api_client.get_init_state()
        api_initialized = api_init_state.get("initialized", False)

        app_instance = current_app.config.get("APPLICATION_INSTANCE")
        if app_instance is None or not getattr(app_instance, "loop", None):
            return jsonify(
                _build_status_response(
                    message="Application is still initializing",
                    api_initialized=api_initialized,
                )
            )

        try:
            future = asyncio.run_coroutine_threadsafe(
                _check_auth_status(api_initialized),
                app_instance.loop,
            )
            return jsonify(future.result(timeout=5.0))
        except concurrent.futures.TimeoutError:
            return jsonify(
                _build_status_response(
                    status="busy",
                    message="Server is busy processing authentication",
                    api_initialized=api_initialized,
                )
            )

    except Exception as e:
        logger.exception("Error getting connection status: %s", e)
        return jsonify(
            _build_status_response(
                status="error",
                error=str(e),
            )
        ), 500


async def _request_new_auth_code() -> dict:
    """Request a new authentication code asynchronously."""
    success = await auth_manager.request_new_code()
    if success:
        return {
            "success": True,
            "auth_code": auth_manager.get_auth_code(),
            "message": "New authentication code generated",
        }
    return {
        "success": False,
        "error": "Failed to generate new authentication code",
    }


@app.route("/api/request-new-code", methods=["POST"])
@login_required
def request_new_code():
    """Request a new authentication code after timeout.

    This endpoint clears the timeout state and generates a new auth code,
    allowing the user to retry the connection process without restarting the app.
    """
    try:
        app_instance = current_app.config.get("APPLICATION_INSTANCE")
        if app_instance is None or not getattr(app_instance, "loop", None):
            return jsonify({"error": "Application is not fully initialized"}), 503

        try:
            future = asyncio.run_coroutine_threadsafe(
                _request_new_auth_code(),
                app_instance.loop,
            )
            result = future.result(timeout=10.0)

            if result.get("success"):
                return jsonify(result)
            return jsonify(result), 500

        except concurrent.futures.TimeoutError:
            return jsonify({"error": "Request timed out"}), 504

    except Exception as e:
        logger.exception("Error requesting new code: %s", e)
        return jsonify({"error": str(e)}), 500


def _ensure_secret_key() -> str:
    """Ensure a secure secret key exists, generating one if needed."""
    secret_key = config.get("web.secret_key", "")

    if secret_key and secret_key != "change-this-to-a-random-secret-key":
        return secret_key

    secret_key = os.urandom(32).hex()
    logger.info("Generated new secret key for session security")

    try:
        with open(config.config_file) as f:
            config_data = yaml.safe_load(f)

        config_data.setdefault("web", {})
        config_data["web"]["secret_key"] = secret_key

        with open(config.config_file, "w") as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

        config.reload()
        logger.info("Secret key saved to configuration file")
    except Exception as e:
        logger.error("Error saving secret key to config: %s", e)

    return secret_key


def _configure_session_cookies(ssl_enabled: bool) -> None:
    """Configure secure session cookie settings."""
    app.config["SESSION_COOKIE_SECURE"] = ssl_enabled
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _configure_production_logging() -> None:
    """Set up rotating file handler for production logging."""
    from logging.handlers import RotatingFileHandler

    log_dir = Path(config.get("general.log_file", "logs/app.log")).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        str(log_dir / "web.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def _log_app_instance_status(app_instance) -> None:
    """Log the status of the application instance and its integrations."""
    if app_instance is None:
        logger.error("Application instance was not passed to start_web_server!")
        return

    if not hasattr(app_instance, "_integrations"):
        logger.error("Passed application instance exists but has no _integrations attribute")
        return

    integrations = app_instance._integrations
    if integrations:
        names = list(integrations.keys())
        logger.info(
            "Web server received application instance with %d integrations: %s",
            len(integrations),
            names,
        )
    else:
        enabled = _get_enabled_integrations()
        if enabled:
            logger.info("Web server started but integrations not loaded yet. Expected: %s", enabled)
        else:
            logger.info("Web server started with no integrations (none enabled in config)")


def _get_ssl_context():
    """Get SSL context if SSL is enabled and configured."""
    if not config.get("web.ssl_enabled", False):
        return None

    cert_file = config.get("web.ssl_cert")
    key_file = config.get("web.ssl_key")

    if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
        logger.info("HTTPS enabled with certificate %s", cert_file)
        return (cert_file, key_file)

    logger.warning("SSL enabled but certificate or key not found")
    return None


def start_web_server(passed_app_instance) -> None:
    """Start the Flask web server."""
    host = config.get("web.host", "0.0.0.0")
    port = config.get("web.port", 5000)
    debug = config.get("web.debug", False)
    ssl_enabled = config.get("web.ssl_enabled", False)

    app.secret_key = _ensure_secret_key()
    _configure_session_cookies(ssl_enabled)

    if not debug:
        _configure_production_logging()

    _log_app_instance_status(passed_app_instance)
    app.config["APPLICATION_INSTANCE"] = passed_app_instance

    app.run(
        host=host,
        port=port,
        debug=debug,
        ssl_context=_get_ssl_context(),
        use_reloader=False,
    )


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    # This part is for direct execution, which is not the main use case.
    # Passing None is appropriate here as there's no main Application instance.
    logger.info("Starting web server directly (not recommended for full app functionality)")
    start_web_server(None)
