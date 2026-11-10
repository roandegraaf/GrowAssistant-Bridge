#!/bin/bash
#
# GrowAssistant Bridge - One-Line Installer
#
# Usage:
#   curl -fsSL https://<your GrowAssistant app>/pi | bash
#       The app's "pair a bridge" screen shows this line. It sets GA_SERVER to
#       that app, so setup only asks for the pairing code shown next to it and
#       installs without further questions.
#
#   curl -fsSL https://raw.githubusercontent.com/roandegraaf/GrowAssistant-Bridge/main/install.sh | bash
#       Plain install with the full interactive setup (GA_SERVER unset).
#
# When piped into bash, stdin is the script itself, so every prompt below reads
# from the terminal (/dev/tty) instead.
#

set -e

# ============================================================================
# Configuration
# ============================================================================

REPO_URL="https://github.com/roandegraaf/GrowAssistant-Bridge.git"
INSTALL_DIR="$HOME/GrowAssistant-Bridge"
BRANCH="main"

# ============================================================================
# Colors
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ============================================================================
# Functions
# ============================================================================

print_success() { echo -e "  ${GREEN}✓${NC} ${1}"; }
print_warning() { echo -e "  ${YELLOW}⚠${NC} ${1}"; }
print_error()   { echo -e "  ${RED}✗${NC} ${1}"; }
print_info()    { echo -e "  ${CYAN}ℹ${NC} ${1}"; }

check_command() { command -v "$1" &>/dev/null; }

# ============================================================================
# Main
# ============================================================================

echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}${BOLD}        GrowAssistant Bridge - Installer                    ${NC}${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check not running as root
if [ "$EUID" -eq 0 ]; then
    print_error "Please run this script as a regular user, not root."
    print_info "The script will use sudo when needed."
    exit 1
fi

# Detect OS and install system packages
if check_command apt-get; then
    print_info "Updating package lists..."
    sudo apt-get update -qq

    PACKAGES=""
    check_command python3 || PACKAGES="$PACKAGES python3"
    check_command pip3 || python3 -m pip --version &>/dev/null 2>&1 || PACKAGES="$PACKAGES python3-pip"
    python3 -m venv --help &>/dev/null 2>&1 || PACKAGES="$PACKAGES python3-venv"
    check_command git || PACKAGES="$PACKAGES git"
    check_command node || PACKAGES="$PACKAGES nodejs"
    check_command npm || PACKAGES="$PACKAGES npm"

    if [ -n "$PACKAGES" ]; then
        print_info "Installing system packages:$PACKAGES"
        sudo apt-get install -y $PACKAGES -qq
        print_success "System packages installed"
    else
        print_success "All system packages already installed"
    fi
elif check_command dnf; then
    PACKAGES=""
    check_command python3 || PACKAGES="$PACKAGES python3"
    check_command pip3 || PACKAGES="$PACKAGES python3-pip"
    check_command git || PACKAGES="$PACKAGES git"
    check_command node || PACKAGES="$PACKAGES nodejs"
    check_command npm || PACKAGES="$PACKAGES npm"

    if [ -n "$PACKAGES" ]; then
        print_info "Installing system packages:$PACKAGES"
        sudo dnf install -y $PACKAGES -q
        print_success "System packages installed"
    else
        print_success "All system packages already installed"
    fi
elif check_command pacman; then
    PACKAGES=""
    check_command python3 || PACKAGES="$PACKAGES python"
    check_command pip3 || PACKAGES="$PACKAGES python-pip"
    check_command git || PACKAGES="$PACKAGES git"
    check_command node || PACKAGES="$PACKAGES nodejs"
    check_command npm || PACKAGES="$PACKAGES npm"

    if [ -n "$PACKAGES" ]; then
        print_info "Installing system packages:$PACKAGES"
        sudo pacman -S --noconfirm $PACKAGES
        print_success "System packages installed"
    else
        print_success "All system packages already installed"
    fi
else
    print_warning "Could not detect package manager. Please ensure these are installed:"
    print_info "  python3, python3-pip, python3-venv, git, nodejs, npm"
    echo ""
    read -p "Continue anyway? [y/N]: " CONTINUE </dev/tty
    case $CONTINUE in
        [Yy]*) ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

# Clone or update repository
echo ""
if [ -d "$INSTALL_DIR/.git" ]; then
    print_info "GrowAssistant Bridge already cloned at $INSTALL_DIR"
    print_info "Pulling latest changes..."
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" 2>/dev/null || {
        print_warning "Could not pull latest changes (local modifications?)"
        print_info "Continuing with existing version"
    }
    print_success "Repository up to date"
else
    if [ -d "$INSTALL_DIR" ]; then
        print_error "$INSTALL_DIR already exists but is not a git repository."
        print_info "Please remove or rename it and try again."
        exit 1
    fi
    print_info "Cloning GrowAssistant Bridge..."
    git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    print_success "Repository cloned to $INSTALL_DIR"
fi

# Run setup script (GA_SERVER, when set, is inherited and switches it to the
# pair-and-go mode)
echo ""
if [ -n "${GA_SERVER:-}" ]; then
    print_info "Setting up and pairing with ${GA_SERVER}..."
else
    print_info "Launching interactive setup..."
fi
echo ""
cd "$INSTALL_DIR"
chmod +x setup.sh
if [ -t 0 ] || [ ! -r /dev/tty ]; then
    exec ./setup.sh
fi
exec ./setup.sh </dev/tty
