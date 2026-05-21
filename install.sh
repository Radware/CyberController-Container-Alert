#!/usr/bin/env bash

################################################################################
# CyberController Container Watchdog - Installation Script
# Version: 1.0.0
#
# Installs the watchdog Docker container that monitors all containers on the
# host and fires alerts (Slack, Splunk, SMTP, SNMP) on crashes, OOM-kills,
# unhealthy states, and restart loops.
#
# Usage:
#   sudo bash install.sh
################################################################################

set -e  # Exit on error

# ── Detect color support ──────────────────────────────────────────────────────
if [ "${FORCE_COLOR:-0}" = "1" ] || ([ -t 1 ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]); then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

# ── Constants ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$SCRIPT_DIR"          # package is already extracted to the install directory
IMAGE_ARCHIVE="${SCRIPT_DIR}/watchdog.tar"
CONTAINER_NAME="watchdog"
IMAGE_NAME="watchdog:latest"
VERSION="1.0.0"

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  ${GREEN}CyberController Container Watchdog - Installation${NC}              ${BLUE}║${NC}"
    echo -e "${BLUE}║${NC}  Version: ${VERSION}                                                 ${BLUE}║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error()   { echo -e "${RED}✗${NC} $1" >&2; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_info()    { echo -e "${BLUE}ℹ${NC} $1"; }

print_section() {
    echo ""
    echo -e "${BLUE}═══${NC} $1"
    echo ""
}

################################################################################
# Pre-flight Checks
################################################################################

check_prerequisites() {
    print_section "Checking Prerequisites"

    # Docker
    if ! command -v docker &>/dev/null; then
        print_error "Docker is not installed or not in PATH"
        echo ""
        echo "  Please install Docker before running this script."
        echo "  See: https://docs.docker.com/get-docker/"
        exit 1
    fi
    print_success "Docker is installed: $(docker --version)"

    if ! docker info &>/dev/null; then
        print_error "Docker daemon is not running"
        echo ""
        echo "  Start it with: sudo systemctl start docker"
        exit 1
    fi
    print_success "Docker daemon is running"

    # Docker Compose (v2 plugin)
    if ! docker compose version &>/dev/null; then
        print_error "Docker Compose plugin not found"
        echo ""
        echo "  Install with: sudo apt-get install docker-compose-plugin"
        echo "  Or see: https://docs.docker.com/compose/install/"
        exit 1
    fi
    print_success "Docker Compose is available: $(docker compose version --short)"

    # Root / sudo warning
    if [ "$EUID" -ne 0 ]; then
        print_warning "Not running as root — you may be prompted for sudo on some steps"
    fi
}

################################################################################
# Log Directory
################################################################################

setup_log_directory() {
    print_section "Setting Up Log Directory"

    LOG_DIR="${INSTALL_DIR}/watchdog"
    if [ ! -d "$LOG_DIR" ]; then
        mkdir -p "$LOG_DIR"
        print_success "Created log directory: ${LOG_DIR}"
    else
        print_info "Log directory already exists: ${LOG_DIR}"
    fi
    print_info "Logs will be written to: ${LOG_DIR}/watchdog.log"
}

################################################################################
# Docker Image
################################################################################

load_docker_image() {
    print_section "Loading Docker Image"

    # Check if image is already present
    if docker image inspect "${IMAGE_NAME}" &>/dev/null; then
        print_info "Image ${IMAGE_NAME} already exists locally"
        read -p "  Re-load from watchdog.tar? [y/N]: " RELOAD
        if [[ ! "$RELOAD" =~ ^[Yy]$ ]]; then
            print_info "Using existing image"
            return 0
        fi
    fi

    if [ ! -f "$IMAGE_ARCHIVE" ]; then
        print_error "Image archive not found: ${IMAGE_ARCHIVE}"
        echo ""
        echo "  Ensure watchdog.tar is in the same directory as install.sh"
        exit 1
    fi

    print_info "Loading image from archive: $(basename "$IMAGE_ARCHIVE")"
    docker load -i "$IMAGE_ARCHIVE"
    print_success "Image loaded: ${IMAGE_NAME}"
}

################################################################################
# Environment / Secrets Configuration
################################################################################

configure_environment() {
    print_section "Environment Configuration"

    ENV_FILE="${INSTALL_DIR}/.env"
    ENV_EXAMPLE="${INSTALL_DIR}/.env.example"

    if [ -f "$ENV_FILE" ]; then
        print_warning "Secrets file already exists: ${ENV_FILE}"
        read -p "  Reconfigure? [y/N]: " RECONFIGURE
        if [[ ! "$RECONFIGURE" =~ ^[Yy]$ ]]; then
            print_info "Using existing .env"
            return 0
        fi
        BACKUP="${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$ENV_FILE" "$BACKUP"
        print_info "Backed up existing .env to: $(basename "$BACKUP")"
    fi

    if [ ! -f "$ENV_EXAMPLE" ]; then
        print_error ".env.example not found in package"
        exit 1
    fi
    cp "$ENV_EXAMPLE" "$ENV_FILE"

    echo ""
    print_info "Configure alert credentials (press Enter to skip optional values):"
    echo ""

    # Slack
    read -p "  Slack Webhook URL (required for Slack alerts): " SLACK_URL
    if [ -n "$SLACK_URL" ]; then
        sed -i "s|^SLACK_WEBHOOK_URL=.*|SLACK_WEBHOOK_URL=${SLACK_URL}|" "$ENV_FILE"
        print_success "Slack webhook configured"
    else
        print_warning "Slack URL left empty — Slack alerts will not fire until set"
    fi

    # Splunk (optional)
    echo ""
    read -p "  Splunk HEC URL (optional, press Enter to skip): " SPLUNK_URL
    if [ -n "$SPLUNK_URL" ]; then
        read -p "  Splunk HEC Token: " SPLUNK_TOKEN
        sed -i "s|^SPLUNK_HEC_URL=.*|SPLUNK_HEC_URL=${SPLUNK_URL}|" "$ENV_FILE"
        sed -i "s|^SPLUNK_HEC_TOKEN=.*|SPLUNK_HEC_TOKEN=${SPLUNK_TOKEN}|" "$ENV_FILE"
        print_success "Splunk HEC configured"
    else
        print_info "Splunk skipped — enable later by editing ${ENV_FILE}"
    fi

    # SMTP (optional)
    echo ""
    read -p "  SMTP Password (optional, press Enter to skip): " -s SMTP_PASS
    echo ""
    if [ -n "$SMTP_PASS" ]; then
        sed -i "s|^SMTP_PASSWORD=.*|SMTP_PASSWORD=${SMTP_PASS}|" "$ENV_FILE"
        print_success "SMTP password configured"
    else
        print_info "SMTP skipped — enable later by editing ${ENV_FILE}"
    fi

    chmod 600 "$ENV_FILE"
    print_success "Secrets file created: ${ENV_FILE} (permissions: 600)"
    echo ""
    print_info "Review alert channels and thresholds in: ${INSTALL_DIR}/watchdog-config.yaml"
}

################################################################################
# Start Watchdog
################################################################################

start_watchdog() {
    print_section "Starting Watchdog"

    cd "${INSTALL_DIR}"

    # If container already exists, ask before replacing
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_warning "Container '${CONTAINER_NAME}' already exists"
        CURRENT_STATUS=$(docker ps -a --filter "name=^${CONTAINER_NAME}$" --format '{{.Status}}')
        print_info "Current status: ${CURRENT_STATUS}"
        read -p "  Stop and recreate it? [y/N]: " RECREATE
        if [[ "$RECREATE" =~ ^[Yy]$ ]]; then
            print_info "Stopping existing container..."
            docker compose down
        else
            print_info "Keeping existing container"
            return 0
        fi
    fi

    print_info "Starting container with docker compose..."
    docker compose up -d
    print_success "Watchdog container started"
}

################################################################################
# Verify
################################################################################

verify_deployment() {
    print_section "Verifying Deployment"

    sleep 2   # give the container a moment to initialise

    STATUS=$(docker ps --filter "name=^${CONTAINER_NAME}$" --format '{{.Status}}' 2>/dev/null || echo "")
    if [ -n "$STATUS" ]; then
        print_success "Container is running: ${STATUS}"
    else
        print_warning "Container does not appear to be running — check logs for errors:"
        print_info "  docker compose logs watchdog"
    fi
}

################################################################################
# Post-Installation Instructions
################################################################################

display_usage_instructions() {
    print_section "Installation Complete!"

    echo -e "${GREEN}✓ Installation successful!${NC}"
    echo ""
    echo -e "${BLUE}═══ Installation Summary ═══${NC}"
    echo "  • Installation directory : ${INSTALL_DIR}"
    echo "  • Container name         : ${CONTAINER_NAME}"
    echo "  • Docker image           : ${IMAGE_NAME}"
    echo "  • Secrets file           : ${INSTALL_DIR}/.env"
    echo "  • Configuration          : ${INSTALL_DIR}/watchdog-config.yaml"
    echo "  • Log file               : ${INSTALL_DIR}/watchdog/watchdog.log"
    echo ""
    echo -e "${BLUE}═══ Common Commands ═══${NC}"
    echo ""
    echo -e "${GREEN}View live logs:${NC}"
    echo "  tail -f ${INSTALL_DIR}/watchdog/watchdog.log"
    echo ""
    echo -e "${GREEN}Container status:${NC}"
    echo "  docker compose -f ${INSTALL_DIR}/docker-compose.yaml ps"
    echo ""
    echo -e "${GREEN}Restart watchdog:${NC}"
    echo "  docker compose -f ${INSTALL_DIR}/docker-compose.yaml restart"
    echo ""
    echo -e "${GREEN}View stdout (Docker logs):${NC}"
    echo "  docker logs -f ${CONTAINER_NAME}"
    echo ""
    echo -e "${GREEN}Edit alert channels / thresholds:${NC}"
    echo "  nano ${INSTALL_DIR}/watchdog-config.yaml"
    echo "  docker compose -f ${INSTALL_DIR}/docker-compose.yaml restart"
    echo ""
    echo -e "${GREEN}Edit secrets (Slack / Splunk / SMTP):${NC}"
    echo "  nano ${INSTALL_DIR}/.env"
    echo "  docker compose -f ${INSTALL_DIR}/docker-compose.yaml up -d"
    echo ""
    echo -e "${GREEN}Uninstall:${NC}"
    echo "  sudo bash ${INSTALL_DIR}/uninstall.sh"
    echo ""
}

################################################################################
# Main
################################################################################

main() {
    print_header
    check_prerequisites
    setup_log_directory
    load_docker_image
    configure_environment
    start_watchdog
    verify_deployment
    display_usage_instructions
}

main "$@"
