"""
Web Application Module.

This module provides a Flask web interface for the GrowAssistant Bridge.
"""

import asyncio
import logging
import os
import time
import yaml
import concurrent.futures
from typing import Any, Dict, List
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED # Import concurrent futures
import threading

from flask import Flask, jsonify, render_template, request, session, redirect, url_for, abort, flash, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import config
from app.registry import registry
from app.auth import auth_manager


logger = logging.getLogger(__name__)

app = Flask(__name__, 
            template_folder=os.path.join(os.path.dirname(__file__), "templates"),
            static_folder=os.path.join(os.path.dirname(__file__), "static"))


# Security middleware
def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip auth check if auth is disabled in config
        if not config.get("web.auth_enabled", False):
            return f(*args, **kwargs)
            
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def is_password_set():
    """Check if a password has been set in the configuration."""
    password_hash = config.get("web.password_hash", "")
    # Consider password not set if it's empty or the default hash for "admin"
    default_hash = generate_password_hash("admin")
    return password_hash and password_hash != default_hash


@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors."""
    return render_template("error.html", error="Page not found", code=404), 404


@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {str(e)}")
    return render_template("error.html", error="Internal server error", code=500), 500


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First-time setup page to set password."""
    # If password is already set, redirect to login
    if is_password_set():
        return redirect(url_for("login"))
        
    error = None
    
    if request.method == "POST":
        username = request.form.get("username", "admin")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        
        if not password:
            error = "Password is required"
        elif password != confirm_password:
            error = "Passwords do not match"
        else:
            # Set the password in config
            try:
                # Generate password hash
                password_hash = generate_password_hash(password)
                
                # Read current config
                with open(config.config_file, "r") as f:
                    config_data = yaml.safe_load(f)
                
                # Update web section
                if "web" not in config_data:
                    config_data["web"] = {}
                    
                config_data["web"]["username"] = username
                config_data["web"]["password_hash"] = password_hash
                
                # Write updated config
                with open(config.config_file, "w") as f:
                    yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
                
                # Update config in memory
                config.reload()
                
                # Redirect to login
                return redirect(url_for("login"))
                
            except Exception as e:
                logger.error(f"Error setting password: {e}")
                error = f"Error setting password: {str(e)}"
    
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    # Skip auth if disabled in config
    if not config.get("web.auth_enabled", False):
        session["logged_in"] = True
        return redirect(url_for("index"))
    
    # If no password is set, redirect to setup
    if not is_password_set():
        return redirect(url_for("setup"))
        
    error = None
    
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        stored_username = config.get("web.username", "admin")
        stored_password_hash = config.get("web.password_hash", "")
        
        if username == stored_username and check_password_hash(stored_password_hash, password):
            session["logged_in"] = True
            next_url = request.args.get("next", url_for("index"))
            return redirect(next_url)
        else:
            error = "Invalid username or password"
            logger.warning(f"Failed login attempt for user: {username}")
    
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """Logout."""
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    """Render the index page with onboarding status."""
    # Check if we need to show onboarding
    auth_code = auth_manager.get_auth_code()
    is_authenticated = auth_manager.is_authenticated()
    
    return render_template(
        "index.html", 
        show_onboarding=auth_code is not None,
        auth_code=auth_code,
        is_authenticated=is_authenticated
    )


@app.route("/", methods=["POST"])
@login_required
def handle_root_post():
    """Handle unexpected POST requests to the root URL."""
    logger.warning(f"Received unexpected POST request to root URL: {request.data}")
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
        
        # Get actions for each device type
        result = {}
        for device_type in device_types:
            actions = registry.get_device_actions(device_type)
            result[device_type] = actions
        
        logger.info(f"Device types response: {result}")
            
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting device types: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/integrations", methods=["GET"])
@login_required
def get_integrations():
    """Get information about loaded integrations."""
    try:
        # Get the existing Application instance from Flask app context
        app_instance = current_app.config.get('APPLICATION_INSTANCE')

        # Check if the application instance exists
        if app_instance is None:
            # This might happen if the web server started before the main app fully initialized
            # or if accessed very early.
            logger.warning("Application instance not found in Flask context for /api/integrations")
            return jsonify({"error": "Application instance not available yet. Please try again."}), 503

        # Check if integrations attribute exists
        if not hasattr(app_instance, '_integrations'):
            logger.error("Application instance has no _integrations attribute")
            return jsonify({"error": "Application seems initialized but integrations are missing."}), 500

        integrations = []
        if app_instance._integrations:
            for name, integration in app_instance._integrations.items():
                try:
                    integration_info = {
                        "name": name,
                        "type": integration.__class__.__name__,
                        "status": "active" # Assuming active if loaded
                    }
                    integrations.append(integration_info)
                    logger.debug(f"Found integration: {name} ({integration.__class__.__name__})")
                except Exception as e:
                    logger.error(f"Error processing integration {name}: {e}")
                    continue
        else:
            logger.info("No integrations currently loaded in Application instance.") # Changed from warning

        logger.info(f"Returning {len(integrations)} integrations")
        return jsonify(integrations)
    except Exception as e:
        logger.exception(f"Error getting integrations: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/queue", methods=["GET"])
@login_required
def get_queue_info():
    """Get information about the queue."""
    try:
        from app.queue_manager import queue_manager
        
        info = {
            "size": queue_manager.size(),
            "empty": queue_manager.is_empty(),
        }
        
        return jsonify(info)
    except Exception as e:
        logger.error(f"Error getting queue info: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/devices", methods=["GET"])
@login_required
def get_devices():
    """Get the current data/state for all registered devices across integrations."""
    try:
        app_instance = current_app.config.get('APPLICATION_INSTANCE')
        if app_instance is None:
            logger.warning("Application instance not found in Flask context for /api/devices")
            return jsonify({"error": "Application instance not available yet."}), 503

        if not hasattr(app_instance, '_integrations'):
            logger.error("Application instance has no _integrations attribute")
            return jsonify({"error": "Integrations not found in application."}), 500
        
        all_device_data = {}
        if app_instance._integrations:
            if not app_instance.loop:
                logger.error("Main application event loop not found on app_instance!")
                return jsonify({"error": "Internal server error: Event loop missing."}), 500

            futures = []
            integration_names = []
            
            # Schedule coroutines on the main event loop
            for integration_name, integration in app_instance._integrations.items():
                coro = integration.get_device_data()
                future = asyncio.run_coroutine_threadsafe(coro, app_instance.loop)
                futures.append(future)
                integration_names.append(integration_name)
            
            # Wait for all futures to complete (with a timeout)
            done, not_done = wait(futures, timeout=10.0) # 10 second timeout

            if not_done:
                 logger.warning(f"Timeout waiting for device data from {len(not_done)} integrations.")
                 # Handle timed out futures - maybe mark them as error?
                 for i, future in enumerate(futures):
                     if future in not_done:
                         integration_name = integration_names[i]
                         all_device_data[integration_name] = {"error": "Timeout getting data"}

            # Process completed results
            for i, future in enumerate(futures):
                 if future in done:
                    integration_name = integration_names[i]
                    try:
                        result = future.result() # Get result from future
                        if isinstance(result, dict):
                            # Add integration name as a prefix to avoid key collisions
                            for device_name, device_info in result.items():
                                all_device_data[f"{integration_name}.{device_name}"] = device_info
                        else:
                             logger.warning(f"Unexpected data type returned from {integration_name}.get_device_data: {type(result)}")
                    except Exception as e:
                        logger.error(f"Error getting result from integration {integration_name}: {e}")
                        all_device_data[integration_name] = {"error": str(e)}

        return jsonify(all_device_data)
    except Exception as e:
        logger.exception(f"Error getting device data: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    """Get the current configuration."""
    try:
        with open(config.config_file, "r") as f:
            config_data = yaml.safe_load(f)
            
        # Remove sensitive data
        if "api" in config_data and "auth_token" in config_data["api"]:
            config_data["api"]["auth_token"] = "**********" if config_data["api"]["auth_token"] else ""
            
        if "web" in config_data:
            if "password_hash" in config_data["web"]:
                config_data["web"]["password_hash"] = "**********"
            if "secret_key" in config_data["web"]:
                config_data["web"]["secret_key"] = "**********"
            
        if "mqtt" in config_data.get("integrations", {}):
            if "password" in config_data["integrations"]["mqtt"]:
                config_data["integrations"]["mqtt"]["password"] = "**********" if config_data["integrations"]["mqtt"]["password"] else ""
        
        return jsonify(config_data)
    except Exception as e:
        logger.error(f"Error reading configuration: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/config", methods=["POST"])
@login_required
def update_config():
    """Update the configuration."""
    try:
        config_data = request.json
        if not config_data:
            return jsonify({"error": "No data provided"}), 400
            
        # Read the current config to get sensitive values
        with open(config.config_file, "r") as f:
            current_config = yaml.safe_load(f)
            
        # Restore sensitive values if they were masked
        if "api" in config_data and "auth_token" in config_data["api"]:
            if config_data["api"]["auth_token"] == "**********":
                config_data["api"]["auth_token"] = current_config.get("api", {}).get("auth_token", "")
                
        if "web" in config_data:
            if "password_hash" in config_data["web"] and config_data["web"]["password_hash"] == "**********":
                config_data["web"]["password_hash"] = current_config.get("web", {}).get("password_hash", "")
            if "secret_key" in config_data["web"] and config_data["web"]["secret_key"] == "**********":
                config_data["web"]["secret_key"] = current_config.get("web", {}).get("secret_key", "")
                
        if "mqtt" in config_data.get("integrations", {}):
            if "password" in config_data["integrations"]["mqtt"] and config_data["integrations"]["mqtt"]["password"] == "**********":
                config_data["integrations"]["mqtt"]["password"] = current_config.get("integrations", {}).get("mqtt", {}).get("password", "")
            
        # Create backup of current config
        backup_file = f"{config.config_file}.bak"
        try:
            with open(config.config_file, "r") as src, open(backup_file, "w") as dst:
                dst.write(src.read())
        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return jsonify({"error": f"Failed to create backup: {str(e)}"}), 500
            
        # Write new config
        try:
            with open(config.config_file, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error(f"Error writing configuration: {e}")
            return jsonify({"error": f"Failed to write configuration: {str(e)}"}), 500
            
        # Notify user that changes require restart
        return jsonify({
            "success": True,
            "message": "Configuration updated. Restart the application for changes to take effect."
        })
    except Exception as e:
        logger.error(f"Error updating configuration: {e}")
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
        payload = data.get("payload", {})
        
        if not action or not target:
            return jsonify({"error": "Missing action or target"}), 400
            
        # Check if there's an integration for this action
        action_key = f"{action}_{target}"
        if not registry.has_integration_for_action(action_key):
            return jsonify({"error": f"No integration found for action: {action_key}"}), 404
            
        # Add command to queue for processing
        from app.api_client import api_client
        
        command = {
            "id": "web-" + str(int(time.time())),
            "action": action,
            "target": target,
            "payload": payload,
        }
        
        asyncio.create_task(api_client._command_queue.put(command))
        
        return jsonify({"success": True, "message": "Command sent for processing"})
    except Exception as e:
        logger.error(f"Error sending command: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/restart", methods=["POST"])
@login_required
def restart_server():
    """Restart the application server.
    
    This endpoint will initiate a graceful shutdown of the current server
    and restart the application. The client should expect a brief disconnection.
    """
    try:
        app_instance = current_app.config.get('APPLICATION_INSTANCE')
        if app_instance is None:
            logger.warning("Application instance not found in Flask context for restart request")
            return jsonify({"error": "Application instance not available."}), 503
        
        # Set a flag to restart in a separate thread to allow the response to be sent
        def restart_app():
            # Import needed modules inside the function to ensure they're available in this scope
            import os
            import time
            import asyncio

            logger.info("Initiating application restart...")
            # Wait a moment to allow the response to be sent
            time.sleep(2)
            
            # Check if this is a watchdog-managed process
            is_watchdog_managed = os.environ.get('WATCHDOG_MANAGED', '0') == '1'
            restart_requested = False
            
            # Try to use the watchdog to restart
            try:
                from app.watchdog import watchdog_manager
                logger.info("Requesting restart via watchdog")
                restart_requested = watchdog_manager.request_restart()
            except ImportError:
                logger.warning("Watchdog not available, using fallback restart method")
            
            # If we couldn't request a restart via watchdog, use the fallback method
            if not restart_requested:
                logger.info("Using fallback restart method (exit code 42)")
                # Perform graceful shutdown
                if app_instance.loop:
                    asyncio.run_coroutine_threadsafe(app_instance.stop(), app_instance.loop)
                    time.sleep(2)  # Wait for stop to complete
                    
                    # Exit with code 42 to signal a restart
                    logger.info("Exiting with code 42 to trigger restart")
                    os._exit(42)
            
        # Start the restart process in a separate thread
        restart_thread = threading.Thread(target=restart_app)
        restart_thread.daemon = True
        restart_thread.start()
        
        return jsonify({
            "success": True,
            "message": "Server restart initiated. The server will be unavailable briefly."
        })
    except Exception as e:
        logger.error(f"Error restarting server: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/connection-status", methods=["GET"])
@login_required
def get_connection_status():
    """Get the current connection status of the application."""
    try:
        from app.api_client import api_client
        
        # Check if the API client has been initialized
        api_init_state = api_client.get_init_state()
        
        # Use an async function to run the auth_manager.check_connection_status method
        async def check_status():
            # Check first if auth_manager has been initialized
            if hasattr(auth_manager, '_client') and auth_manager._client is not None:
                if auth_manager.is_authenticated():
                    connected, status = await auth_manager.check_connection_status()
                    auth_code = auth_manager.get_auth_code()
                    client_id = auth_manager.get_client_id()
                    
                    return {
                        "authenticated": True,
                        "connected": connected,
                        "status": status,
                        "auth_code": auth_code,
                        "client_id": client_id,
                        "ready": auth_manager.is_ready_for_data(),
                        "api_client_initialized": api_init_state.get("initialized", False)
                    }
                else:
                    # Check if the client is in the registration process
                    auth_code = auth_manager.get_auth_code()
                    if auth_code:
                        return {
                            "authenticated": False,
                            "connected": False,
                            "status": "registration",
                            "auth_code": auth_code,
                            "ready": False,
                            "api_client_initialized": api_init_state.get("initialized", False)
                        }
                    else:
                        return {
                            "authenticated": False,
                            "connected": False,
                            "status": "not_registered",
                            "ready": False,
                            "api_client_initialized": api_init_state.get("initialized", False)
                        }
            else:
                # Auth manager hasn't been fully initialized yet
                return {
                    "authenticated": False,
                    "connected": False,
                    "status": "initializing",
                    "message": "Authentication manager is initializing",
                    "ready": False,
                    "api_client_initialized": api_init_state.get("initialized", False)
                }
        
        # Get the application instance to use its event loop
        app_instance = current_app.config.get('APPLICATION_INSTANCE')
        if app_instance is None or not hasattr(app_instance, 'loop') or app_instance.loop is None:
            # Fall back to a simple status if we can't use the event loop
            return jsonify({
                "authenticated": False,
                "connected": False,
                "status": "initializing",
                "message": "Application is still initializing",
                "ready": False,
                "api_client_initialized": api_init_state.get("initialized", False)
            })
        
        # Run the async function in the application's event loop
        try:
            future = asyncio.run_coroutine_threadsafe(check_status(), app_instance.loop)
            status_info = future.result(timeout=5.0)  # 5 second timeout
            return jsonify(status_info)
        except concurrent.futures.TimeoutError:
            # If the operation times out, the server might be busy with authentication
            return jsonify({
                "authenticated": False,
                "connected": False,
                "status": "busy",
                "message": "Server is busy processing authentication",
                "ready": False,
                "api_client_initialized": api_init_state.get("initialized", False)
            })
            
    except Exception as e:
        logger.exception(f"Error getting connection status: {e}")
        return jsonify({
            "authenticated": False, 
            "connected": False, 
            "status": "error",
            "error": str(e),
            "ready": False,
            "api_client_initialized": False
        }), 500


def start_web_server(passed_app_instance):
    """Start the Flask web server."""
    host = config.get("web.host", "0.0.0.0")
    port = config.get("web.port", 5000)
    debug = config.get("web.debug", False)
    
    # Set secret key for session
    app.secret_key = config.get("web.secret_key", os.urandom(24))
    
    # Configure logging for production
    if not debug:
        from logging.handlers import RotatingFileHandler
        log_dir = os.path.dirname(config.get("general.log_file", "logs/app.log"))
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            
        handler = RotatingFileHandler(
            os.path.join(log_dir, "web.log"),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5
        )
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    
    # Use the passed application instance directly
    if passed_app_instance is None:
        logger.error("Application instance was not passed to start_web_server!")
    elif not hasattr(passed_app_instance, '_integrations'):
        logger.error("Passed application instance exists but has no _integrations attribute")
    else:
        integrations_count = len(passed_app_instance._integrations)
        logger.info(f"Web server received application instance with {integrations_count} integrations.")

    # Store the instance for endpoint use (using Flask's app context)
    app.config['APPLICATION_INSTANCE'] = passed_app_instance

    # Set up HTTPS if configured
    ssl_context = None
    if config.get("web.ssl_enabled", False):
        cert_file = config.get("web.ssl_cert")
        key_file = config.get("web.ssl_key")
        if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
            ssl_context = (cert_file, key_file)
            logger.info(f"HTTPS enabled with certificate {cert_file}")
        else:
            logger.warning("SSL enabled but certificate or key not found")
    
    # Fix: Disable Flask reloader in debug mode since we manage our own processes
    app.run(
        host=host, 
        port=port, 
        debug=debug, 
        ssl_context=ssl_context,
        use_reloader=False  # Disable Flask's reloader
    )


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    # This part is for direct execution, which is not the main use case.
    # Passing None is appropriate here as there's no main Application instance.
    logger.info("Starting web server directly (not recommended for full app functionality)")
    start_web_server(None) 