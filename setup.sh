#!/bin/bash
#
# GrowAssistant Bridge - Interactive Setup Script
# For Raspberry Pi deployment
#

set -e

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
CONFIG_FILE="$SCRIPT_DIR/config.yaml"
CONFIG_EXAMPLE="$SCRIPT_DIR/config.example.yaml"
SERVICE_NAME="growassistant"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Default values
DEFAULT_PORT=5000
DEFAULT_USERNAME="admin"

# ============================================================================
# Colors and Formatting
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}${BOLD}          GrowAssistant Bridge - Setup Script              ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo -e "${BLUE}[${1}]${NC} ${BOLD}${2}${NC}"
}

print_success() {
    echo -e "  ${GREEN}✓${NC} ${1}"
}

print_warning() {
    echo -e "  ${YELLOW}⚠${NC} ${1}"
}

print_error() {
    echo -e "  ${RED}✗${NC} ${1}"
}

print_info() {
    echo -e "  ${CYAN}ℹ${NC} ${1}"
}

check_command() {
    command -v "$1" &> /dev/null
}

# Check if whiptail is available
has_whiptail() {
    check_command whiptail
}

# ============================================================================
# Whiptail UI Functions
# ============================================================================

show_welcome() {
    if has_whiptail; then
        whiptail --title "GrowAssistant Bridge Setup" --msgbox \
"Welcome to GrowAssistant Bridge Setup!

This script will guide you through:

  1. Installing Python dependencies
  2. Selecting integrations (GPIO, MQTT, etc.)
  3. Configuring the web interface
  4. Setting up auto-start (optional)

Navigation:
  • Use ARROW KEYS to move
  • Press ENTER to confirm
  • Press ESC to cancel

Press OK to continue." 20 55
    else
        print_header
        echo "Welcome to GrowAssistant Bridge Setup!"
        echo ""
        echo "This script will guide you through the installation."
        echo "Press Enter to continue or Ctrl+C to cancel."
        read -r
    fi
}

select_integrations() {
    if has_whiptail; then
        INTEGRATIONS=$(whiptail --title "Integration Selection" --checklist \
"Select which integrations to enable.

Use ARROW KEYS to move, SPACE to select/deselect,
TAB to switch to buttons, and ENTER to confirm." 18 65 5 \
"gpio" "Raspberry Pi GPIO pins (sensors/relays)" OFF \
"mqtt" "Connect to MQTT broker" OFF \
"http" "Poll HTTP endpoints" OFF \
"serial" "Serial port devices" OFF \
"sample" "Demo data for testing (recommended)" ON \
3>&1 1>&2 2>&3) || return 1

        # Convert to space-separated list without quotes
        INTEGRATIONS=$(echo "$INTEGRATIONS" | tr -d '"')
    else
        echo ""
        print_step "1/5" "Integration Selection"
        echo ""
        echo "Select integrations to enable (enter numbers separated by spaces):"
        echo "  1) GPIO   - Raspberry Pi GPIO pins (sensors/relays)"
        echo "  2) MQTT   - Connect to MQTT broker"
        echo "  3) HTTP   - Poll HTTP endpoints"
        echo "  4) Serial - Serial port devices"
        echo "  5) Sample - Demo data for testing (recommended)"
        echo ""
        read -p "Enter choices [5]: " choices
        choices=${choices:-5}

        INTEGRATIONS=""
        for c in $choices; do
            case $c in
                1) INTEGRATIONS="$INTEGRATIONS gpio" ;;
                2) INTEGRATIONS="$INTEGRATIONS mqtt" ;;
                3) INTEGRATIONS="$INTEGRATIONS http" ;;
                4) INTEGRATIONS="$INTEGRATIONS serial" ;;
                5) INTEGRATIONS="$INTEGRATIONS sample" ;;
            esac
        done
    fi
}

configure_mqtt() {
    if has_whiptail; then
        MQTT_BROKER=$(whiptail --title "MQTT Configuration" --inputbox \
"Enter MQTT broker address:

Use ARROW KEYS to move, ENTER to confirm." 12 50 "localhost" 3>&1 1>&2 2>&3) || MQTT_BROKER="localhost"

        MQTT_PORT=$(whiptail --title "MQTT Configuration" --inputbox \
"Enter MQTT broker port:" 10 50 "1883" 3>&1 1>&2 2>&3) || MQTT_PORT="1883"

        MQTT_USERNAME=$(whiptail --title "MQTT Configuration" --inputbox \
"Enter MQTT username (leave empty for none):" 10 50 "" 3>&1 1>&2 2>&3) || MQTT_USERNAME=""

        if [ -n "$MQTT_USERNAME" ]; then
            MQTT_PASSWORD=$(whiptail --title "MQTT Configuration" --passwordbox \
"Enter MQTT password:" 10 50 3>&1 1>&2 2>&3) || MQTT_PASSWORD=""
        else
            MQTT_PASSWORD=""
        fi
    else
        echo ""
        echo "MQTT Configuration:"
        read -p "  Broker address [localhost]: " MQTT_BROKER
        MQTT_BROKER=${MQTT_BROKER:-localhost}
        read -p "  Broker port [1883]: " MQTT_PORT
        MQTT_PORT=${MQTT_PORT:-1883}
        read -p "  Username (leave empty for none): " MQTT_USERNAME
        if [ -n "$MQTT_USERNAME" ]; then
            read -s -p "  Password: " MQTT_PASSWORD
            echo ""
        fi
    fi
}

configure_web_interface() {
    if has_whiptail; then
        WEB_USERNAME=$(whiptail --title "Web Interface Setup" --inputbox \
"Enter admin username:

Use ARROW KEYS to move, ENTER to confirm." 12 50 "$DEFAULT_USERNAME" 3>&1 1>&2 2>&3) || WEB_USERNAME="$DEFAULT_USERNAME"

        while true; do
            WEB_PASSWORD=$(whiptail --title "Web Interface Setup" --passwordbox \
"Enter admin password (min 4 characters):" 10 50 3>&1 1>&2 2>&3) || return 1

            if [ ${#WEB_PASSWORD} -ge 4 ]; then
                WEB_PASSWORD_CONFIRM=$(whiptail --title "Web Interface Setup" --passwordbox \
"Confirm admin password:" 10 50 3>&1 1>&2 2>&3) || return 1

                if [ "$WEB_PASSWORD" = "$WEB_PASSWORD_CONFIRM" ]; then
                    break
                else
                    whiptail --title "Error" --msgbox "Passwords do not match. Please try again." 8 45
                fi
            else
                whiptail --title "Error" --msgbox "Password must be at least 4 characters." 8 45
            fi
        done

        WEB_PORT=$(whiptail --title "Web Interface Setup" --inputbox \
"Enter web interface port:" 10 50 "$DEFAULT_PORT" 3>&1 1>&2 2>&3) || WEB_PORT="$DEFAULT_PORT"

        if whiptail --title "External Access" --yesno \
"Allow external access to web interface?

Selecting YES will make the dashboard accessible
from other devices on your network.

Selecting NO will only allow access from this device." 12 55; then
            WEB_EXTERNAL="yes"
        else
            WEB_EXTERNAL="no"
        fi
    else
        echo ""
        print_step "2/5" "Web Interface Configuration"
        echo ""
        read -p "  Admin username [$DEFAULT_USERNAME]: " WEB_USERNAME
        WEB_USERNAME=${WEB_USERNAME:-$DEFAULT_USERNAME}

        while true; do
            read -s -p "  Admin password (min 4 characters): " WEB_PASSWORD
            echo ""
            if [ ${#WEB_PASSWORD} -ge 4 ]; then
                read -s -p "  Confirm password: " WEB_PASSWORD_CONFIRM
                echo ""
                if [ "$WEB_PASSWORD" = "$WEB_PASSWORD_CONFIRM" ]; then
                    break
                else
                    print_error "Passwords do not match. Try again."
                fi
            else
                print_error "Password must be at least 4 characters."
            fi
        done

        read -p "  Web interface port [$DEFAULT_PORT]: " WEB_PORT
        WEB_PORT=${WEB_PORT:-$DEFAULT_PORT}

        read -p "  Allow external access? [Y/n]: " WEB_EXTERNAL
        WEB_EXTERNAL=${WEB_EXTERNAL:-Y}
        case $WEB_EXTERNAL in
            [Yy]*) WEB_EXTERNAL="yes" ;;
            *) WEB_EXTERNAL="no" ;;
        esac
    fi
}

ask_systemd_setup() {
    if has_whiptail; then
        if whiptail --title "Auto-Start Setup" --yesno \
"Do you want to install a systemd service?

This will automatically start GrowAssistant Bridge
when your Raspberry Pi boots up.

Recommended for production use." 12 55; then
            INSTALL_SERVICE="yes"
        else
            INSTALL_SERVICE="no"
        fi
    else
        echo ""
        print_step "3/5" "Auto-Start Configuration"
        read -p "  Install systemd service for auto-start? [Y/n]: " INSTALL_SERVICE
        INSTALL_SERVICE=${INSTALL_SERVICE:-Y}
        case $INSTALL_SERVICE in
            [Yy]*) INSTALL_SERVICE="yes" ;;
            *) INSTALL_SERVICE="no" ;;
        esac
    fi
}

ask_start_now() {
    if has_whiptail; then
        if whiptail --title "Start Application" --yesno \
"Setup is complete!

Do you want to start GrowAssistant Bridge now?" 10 50; then
            START_NOW="yes"
        else
            START_NOW="no"
        fi
    else
        echo ""
        read -p "Start GrowAssistant Bridge now? [Y/n]: " START_NOW
        START_NOW=${START_NOW:-Y}
        case $START_NOW in
            [Yy]*) START_NOW="yes" ;;
            *) START_NOW="no" ;;
        esac
    fi
}

show_config_exists_prompt() {
    if has_whiptail; then
        CHOICE=$(whiptail --title "Configuration Exists" --menu \
"A config.yaml file already exists.

What would you like to do?" 14 55 3 \
"overwrite" "Replace with new configuration" \
"keep" "Keep existing configuration" \
"backup" "Backup existing and create new" \
3>&1 1>&2 2>&3) || CHOICE="keep"
    else
        echo ""
        print_warning "config.yaml already exists."
        echo "  1) Overwrite with new configuration"
        echo "  2) Keep existing configuration"
        echo "  3) Backup existing and create new"
        read -p "  Choose [2]: " choice
        case ${choice:-2} in
            1) CHOICE="overwrite" ;;
            2) CHOICE="keep" ;;
            3) CHOICE="backup" ;;
            *) CHOICE="keep" ;;
        esac
    fi
}

# ============================================================================
# Installation Functions
# ============================================================================

check_prerequisites() {
    print_step "Checking" "Prerequisites"

    # Check not running as root
    if [ "$EUID" -eq 0 ]; then
        print_error "Please run this script as a regular user, not root."
        print_info "The script will use sudo when needed."
        exit 1
    fi
    print_success "Running as regular user"

    # Check Python version
    if check_command python3; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
        PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 9 ]; then
            print_success "Python $PYTHON_VERSION found"
        else
            print_error "Python 3.9+ required, found $PYTHON_VERSION"
            exit 1
        fi
    else
        print_error "Python 3 not found. Please install python3."
        exit 1
    fi

    # Check for Node.js (optional)
    if check_command node && check_command npm; then
        NODE_VERSION=$(node --version)
        print_success "Node.js $NODE_VERSION found"
        HAS_NODE="yes"
    else
        print_warning "Node.js not found - CSS build will be skipped"
        HAS_NODE="no"
    fi

    # Check for pip
    if check_command pip3 || python3 -m pip --version &>/dev/null; then
        print_success "pip available"
    else
        print_error "pip not found. Please install python3-pip."
        exit 1
    fi

    # Detect Raspberry Pi
    if [ -f /proc/device-tree/model ]; then
        PI_MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')
        if [[ "$PI_MODEL" == *"Raspberry Pi"* ]]; then
            print_success "Running on $PI_MODEL"
            IS_RASPBERRY_PI="yes"
        else
            print_info "Not a Raspberry Pi - GPIO will be simulated"
            IS_RASPBERRY_PI="no"
        fi
    else
        print_info "Not a Raspberry Pi - GPIO will be simulated"
        IS_RASPBERRY_PI="no"
    fi

    # Check internet connectivity
    if ping -c 1 -W 2 pypi.org &>/dev/null; then
        print_success "Internet connection available"
    else
        print_warning "Cannot reach pypi.org - installation may fail"
    fi

    echo ""
}

setup_virtual_environment() {
    print_step "Installing" "Python Virtual Environment"

    if [ -d "$VENV_DIR" ]; then
        print_success "Virtual environment already exists"
    else
        python3 -m venv "$VENV_DIR"
        print_success "Virtual environment created"
    fi

    # Activate venv
    source "$VENV_DIR/bin/activate"
    print_success "Virtual environment activated"

    # Upgrade pip
    pip install --upgrade pip --quiet
    print_success "pip upgraded"

    echo ""
}

install_dependencies() {
    print_step "Installing" "Python Dependencies"

    # Install base requirements
    pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
    print_success "Core dependencies installed"

    # Install GPIO libraries if needed and on Raspberry Pi
    if [[ "$INTEGRATIONS" == *"gpio"* ]]; then
        if [ "$IS_RASPBERRY_PI" = "yes" ]; then
            pip install RPi.GPIO gpiozero --quiet
            print_success "GPIO libraries installed (RPi.GPIO, gpiozero)"
        else
            print_warning "GPIO selected but not on Raspberry Pi - using mock GPIO"
        fi
    fi

    echo ""
}

build_frontend() {
    if [ "$HAS_NODE" = "yes" ]; then
        print_step "Building" "Frontend Assets"

        cd "$SCRIPT_DIR"
        npm install --silent 2>/dev/null
        print_success "npm dependencies installed"

        npm run build:css --silent 2>/dev/null
        print_success "Tailwind CSS built"

        echo ""
    fi
}

generate_password_hash() {
    python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('$1'))"
}

generate_secret_key() {
    python3 -c "import secrets; print(secrets.token_hex(32))"
}

generate_config() {
    print_step "Generating" "Configuration"

    # Handle existing config
    if [ -f "$CONFIG_FILE" ]; then
        show_config_exists_prompt
        case $CHOICE in
            "keep")
                print_info "Keeping existing configuration"
                echo ""
                return
                ;;
            "backup")
                BACKUP_FILE="${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
                cp "$CONFIG_FILE" "$BACKUP_FILE"
                print_success "Backed up to $BACKUP_FILE"
                ;;
            "overwrite")
                print_info "Overwriting configuration"
                ;;
        esac
    fi

    # Generate password hash and secret key
    PASSWORD_HASH=$(generate_password_hash "$WEB_PASSWORD")
    SECRET_KEY=$(generate_secret_key)

    # Determine web host
    if [ "$WEB_EXTERNAL" = "yes" ]; then
        WEB_HOST="0.0.0.0"
    else
        WEB_HOST="127.0.0.1"
    fi

    # Start with example config
    cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"

    # Update web settings using Python for reliable YAML handling
    python3 << EOF
import yaml

with open('$CONFIG_FILE', 'r') as f:
    config = yaml.safe_load(f)

# Web settings
config['web']['username'] = '$WEB_USERNAME'
config['web']['password_hash'] = '''$PASSWORD_HASH'''
config['web']['secret_key'] = '$SECRET_KEY'
config['web']['port'] = $WEB_PORT
config['web']['host'] = '$WEB_HOST'

# Integration settings
config['integrations']['gpio']['enabled'] = $([[ "$INTEGRATIONS" == *"gpio"* ]] && echo "True" || echo "False")
config['integrations']['mqtt']['enabled'] = $([[ "$INTEGRATIONS" == *"mqtt"* ]] && echo "True" || echo "False")
config['integrations']['http']['enabled'] = $([[ "$INTEGRATIONS" == *"http"* ]] && echo "True" || echo "False")
config['integrations']['serial']['enabled'] = $([[ "$INTEGRATIONS" == *"serial"* ]] && echo "True" || echo "False")
config['integrations']['sample']['enabled'] = $([[ "$INTEGRATIONS" == *"sample"* ]] && echo "True" || echo "False")

# MQTT settings if enabled
if config['integrations']['mqtt']['enabled']:
    config['integrations']['mqtt']['broker'] = '${MQTT_BROKER:-localhost}'
    config['integrations']['mqtt']['port'] = ${MQTT_PORT:-1883}
    config['integrations']['mqtt']['username'] = '${MQTT_USERNAME:-}'
    config['integrations']['mqtt']['password'] = '${MQTT_PASSWORD:-}'

with open('$CONFIG_FILE', 'w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
EOF

    print_success "Configuration generated"
    echo ""
}

setup_mdns() {
    print_step "Setting up" "mDNS (hostname.local)"

    if check_command avahi-daemon; then
        print_success "avahi-daemon already installed"
    else
        print_info "Installing avahi-daemon for .local hostname support..."
        sudo apt-get update -qq
        sudo apt-get install -y avahi-daemon -qq
        print_success "avahi-daemon installed"
    fi

    # Ensure avahi-daemon is running
    if systemctl is-active --quiet avahi-daemon; then
        print_success "avahi-daemon is running"
    else
        sudo systemctl enable avahi-daemon --quiet
        sudo systemctl start avahi-daemon
        print_success "avahi-daemon started"
    fi

    echo ""
}

configure_firewall() {
    # Only configure if external access is enabled
    if [ "$WEB_EXTERNAL" != "yes" ]; then
        return
    fi

    print_step "Configuring" "Firewall for external access"

    # Check if ufw is installed and active
    if check_command ufw; then
        UFW_STATUS=$(sudo ufw status 2>/dev/null | head -1)
        if [[ "$UFW_STATUS" == *"active"* ]]; then
            print_info "ufw firewall is active, opening port $WEB_PORT..."
            sudo ufw allow "$WEB_PORT/tcp" comment "GrowAssistant Bridge" >/dev/null 2>&1
            print_success "Port $WEB_PORT opened in ufw firewall"
            echo ""
            return
        else
            print_info "ufw is installed but not active"
        fi
    fi

    # Check if firewalld is installed and active
    if check_command firewall-cmd; then
        if systemctl is-active --quiet firewalld; then
            print_info "firewalld is active, opening port $WEB_PORT..."
            sudo firewall-cmd --permanent --add-port="$WEB_PORT/tcp" >/dev/null 2>&1
            sudo firewall-cmd --reload >/dev/null 2>&1
            print_success "Port $WEB_PORT opened in firewalld"
            echo ""
            return
        fi
    fi

    # Check if iptables has rules that might block traffic
    if check_command iptables; then
        IPTABLES_RULES=$(sudo iptables -L INPUT -n 2>/dev/null | wc -l)
        if [ "$IPTABLES_RULES" -gt 2 ]; then
            # There are custom iptables rules, add one for our port
            print_info "iptables rules detected, adding rule for port $WEB_PORT..."
            sudo iptables -I INPUT -p tcp --dport "$WEB_PORT" -j ACCEPT 2>/dev/null || true
            print_success "Port $WEB_PORT rule added to iptables"
            print_warning "Note: iptables rules are not persistent across reboots"
            print_info "To make persistent, install iptables-persistent:"
            print_info "  sudo apt install iptables-persistent"
            echo ""
            return
        fi
    fi

    # No active firewall detected
    print_success "No active firewall detected - port $WEB_PORT should be accessible"
    echo ""
}

install_systemd_service() {
    if [ "$INSTALL_SERVICE" != "yes" ]; then
        return
    fi

    print_step "Installing" "Systemd Service"

    # Get current user
    CURRENT_USER=$(whoami)

    # Create service file
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=GrowAssistant Bridge
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
Environment=PATH=$VENV_DIR/bin
ExecStart=$VENV_DIR/bin/python -m app.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    print_success "Service file created"

    # Reload systemd
    sudo systemctl daemon-reload
    print_success "Systemd reloaded"

    # Enable service
    sudo systemctl enable "$SERVICE_NAME" --quiet
    print_success "Service enabled for auto-start"

    echo ""
}

get_network_info() {
    # Get hostname
    HOSTNAME=$(hostname)

    # Get IP address (prefer non-loopback IPv4)
    IP_ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -z "$IP_ADDRESS" ]; then
        IP_ADDRESS=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
    fi
    if [ -z "$IP_ADDRESS" ]; then
        IP_ADDRESS="127.0.0.1"
    fi

    # Read actual port and username from config file (in case user kept existing config)
    if [ -f "$CONFIG_FILE" ]; then
        ACTUAL_PORT=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('web',{}).get('port', 5000))" 2>/dev/null)
        ACTUAL_USERNAME=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('web',{}).get('username', 'admin'))" 2>/dev/null)
        ACTUAL_HOST=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('web',{}).get('host', '127.0.0.1'))" 2>/dev/null)

        # Use actual values from config
        WEB_PORT="${ACTUAL_PORT:-$WEB_PORT}"
        WEB_USERNAME="${ACTUAL_USERNAME:-$WEB_USERNAME}"

        # Check if external access is enabled
        if [ "$ACTUAL_HOST" = "0.0.0.0" ]; then
            WEB_EXTERNAL="yes"
        fi
    fi
}

start_application() {
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        print_step "Starting" "GrowAssistant Bridge (via systemd)"
        sudo systemctl start "$SERVICE_NAME"
        sleep 3

        if systemctl is-active --quiet "$SERVICE_NAME"; then
            print_success "Service started successfully"
        else
            print_error "Service failed to start. Check: journalctl -u $SERVICE_NAME"
        fi
    else
        print_step "Starting" "GrowAssistant Bridge"
        echo ""
        print_info "Starting in foreground mode..."
        print_info "Press Ctrl+C to stop"
        echo ""

        cd "$SCRIPT_DIR"
        source "$VENV_DIR/bin/activate"
        python -m app.main &
        APP_PID=$!
        sleep 3

        if kill -0 $APP_PID 2>/dev/null; then
            print_success "Application started (PID: $APP_PID)"
        else
            print_error "Application failed to start"
        fi
    fi
}

show_success_banner() {
    get_network_info

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}${BOLD}        ✓ GrowAssistant Bridge is running!                 ${NC}${GREEN}║${NC}"
    echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  ${BOLD}Access your dashboard at:${NC}                                ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    printf "${GREEN}║${NC}  ${CYAN}• http://%-45s${NC}  ${GREEN}║${NC}\n" "${IP_ADDRESS}:${WEB_PORT}"
    printf "${GREEN}║${NC}  ${CYAN}• http://%-45s${NC}  ${GREEN}║${NC}\n" "${HOSTNAME}:${WEB_PORT}"
    printf "${GREEN}║${NC}  ${CYAN}• http://%-45s${NC}  ${GREEN}║${NC}\n" "${HOSTNAME}.local:${WEB_PORT}"
    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  ${BOLD}Login credentials:${NC}                                        ${GREEN}║${NC}"
    printf "${GREEN}║${NC}    Username: ${YELLOW}%-42s${NC}  ${GREEN}║${NC}\n" "$WEB_USERNAME"
    echo -e "${GREEN}║${NC}    Password: ${YELLOW}(the password you entered)${NC}                   ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"

    if [ "$INSTALL_SERVICE" = "yes" ]; then
        echo -e "${GREEN}║${NC}  ${BOLD}Useful commands:${NC}                                          ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    Stop:    ${CYAN}sudo systemctl stop $SERVICE_NAME${NC}              ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    Start:   ${CYAN}sudo systemctl start $SERVICE_NAME${NC}             ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    Logs:    ${CYAN}journalctl -u $SERVICE_NAME -f${NC}                  ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    Status:  ${CYAN}sudo systemctl status $SERVICE_NAME${NC}            ${GREEN}║${NC}"
    else
        echo -e "${GREEN}║${NC}  ${BOLD}To run manually:${NC}                                          ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    ${CYAN}source venv/bin/activate${NC}                               ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    ${CYAN}python -m app.main${NC}                                     ${GREEN}║${NC}"
    fi

    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [ "$WEB_EXTERNAL" = "yes" ]; then
        print_info "External access is enabled. Make sure your firewall allows port $WEB_PORT."
    fi
}

show_manual_start_instructions() {
    get_network_info

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}${BOLD}           ✓ Setup Complete!                               ${NC}${GREEN}║${NC}"
    echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  ${BOLD}To start GrowAssistant Bridge:${NC}                            ${GREEN}║${NC}"

    if [ "$INSTALL_SERVICE" = "yes" ]; then
        echo -e "${GREEN}║${NC}    ${CYAN}sudo systemctl start $SERVICE_NAME${NC}                      ${GREEN}║${NC}"
    else
        echo -e "${GREEN}║${NC}    ${CYAN}cd $SCRIPT_DIR${NC}"
        echo -e "${GREEN}║${NC}    ${CYAN}source venv/bin/activate${NC}                               ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}    ${CYAN}python -m app.main${NC}                                     ${GREEN}║${NC}"
    fi

    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  ${BOLD}Your dashboard will be available at:${NC}                      ${GREEN}║${NC}"
    printf "${GREEN}║${NC}    ${CYAN}http://%-48s${NC}  ${GREEN}║${NC}\n" "${IP_ADDRESS}:${WEB_PORT}"
    printf "${GREEN}║${NC}    ${CYAN}http://%-48s${NC}  ${GREEN}║${NC}\n" "${HOSTNAME}.local:${WEB_PORT}"
    echo -e "${GREEN}║${NC}                                                            ${GREEN}║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# ============================================================================
# Main Script
# ============================================================================

main() {
    cd "$SCRIPT_DIR"

    # Show welcome message
    show_welcome

    # Check prerequisites
    check_prerequisites

    # Select integrations
    select_integrations || { echo "Setup cancelled."; exit 1; }

    # Configure MQTT if selected
    if [[ "$INTEGRATIONS" == *"mqtt"* ]]; then
        configure_mqtt
    fi

    # Configure web interface
    configure_web_interface || { echo "Setup cancelled."; exit 1; }

    # Ask about systemd service
    ask_systemd_setup

    # Run installation steps
    setup_virtual_environment
    install_dependencies
    build_frontend
    generate_config
    setup_mdns
    configure_firewall
    install_systemd_service

    # Ask if user wants to start now
    ask_start_now

    if [ "$START_NOW" = "yes" ]; then
        start_application
        show_success_banner
    else
        show_manual_start_instructions
    fi
}

# Run main function
main "$@"
