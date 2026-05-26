#!/usr/bin/env bash

################################################################################
# CyberController Container Watchdog - Installation Script
# Version: 1.0.0
#
# Installs the watchdog Docker container that monitors all containers on the
# host and fires alerts (Slack, SMTP, SNMP) on crashes, OOM-kills,
# unhealthy states, and restart loops.
#
# Usage:
#   sudo bash install.sh
################################################################################

# Errors are handled explicitly with exit 1 throughout this script

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
DC=""  # compose command — set automatically in check_prerequisites

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

    # Docker Compose — accept v2 plugin (docker compose) or v1 standalone (docker-compose)
    if docker compose version &>/dev/null 2>&1; then
        DC="docker compose"
        print_success "Docker Compose is available (v2 plugin): $(docker compose version --short)"
    elif command -v docker-compose &>/dev/null; then
        DC="docker-compose"
        print_success "Docker Compose is available (v1 standalone): $(docker-compose --version)"
    else
        print_error "Docker Compose not found"
        echo ""
        echo "  Install the v2 plugin:  sudo apt-get install docker-compose-plugin"
        echo "  Install v1 standalone:  sudo apt-get install docker-compose"
        echo "  Or see: https://docs.docker.com/compose/install/"
        exit 1
    fi

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
        print_info "Image ${IMAGE_NAME} already exists locally — using existing image"
        return 0
    fi

    if [ ! -f "$IMAGE_ARCHIVE" ]; then
        print_warning "Image archive not found: ${IMAGE_ARCHIVE}"
        echo ""
        if [ -f "${INSTALL_DIR}/Dockerfile" ]; then
            print_info "Building image from source — this may take a few minutes..."
            # Use a clean temp dir so any .dockerignore in INSTALL_DIR is bypassed
            local tmpdir
            tmpdir=$(mktemp -d)
            trap "rm -rf '${tmpdir}'" EXIT
            cp "${INSTALL_DIR}/Dockerfile"   "${tmpdir}/"
            cp "${INSTALL_DIR}/watchdog.py"  "${tmpdir}/"
            docker build -t "${IMAGE_NAME}" "${tmpdir}"
            print_success "Image built: ${IMAGE_NAME}"
            return 0
        fi
        print_error "Cannot proceed: watchdog.tar not found and Dockerfile not found"
        echo ""
        echo "  Copy watchdog.tar into: $(dirname "$IMAGE_ARCHIVE") and re-run."
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
    print_section "Alert Channels \& Credentials"

    ENV_FILE="${INSTALL_DIR}/.env"
    ENV_EXAMPLE="${INSTALL_DIR}/.env.example"

    if [ -f "$ENV_FILE" ]; then
        print_info "Using existing secrets file: ${ENV_FILE}"
        return 0
    fi

    if [ ! -f "$ENV_EXAMPLE" ]; then
        print_error ".env.example not found in package"
        exit 1
    fi
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    print_success "Secrets file created: ${ENV_FILE} (permissions: 600)"
    print_info "Edit ${ENV_FILE} to add credentials, then restart: $DC restart watchdog"
}

################################################################################
# Watchdog YAML Configuration
################################################################################

configure_watchdog_yaml() {
    print_section "Watchdog Settings (watchdog-config.yaml)"

    CONFIG_FILE="${INSTALL_DIR}/watchdog-config.yaml"

    if [ -f "$CONFIG_FILE" ]; then
        print_info "Using existing watchdog-config.yaml"
        return 0
    fi

    # Write yaml with default values
    {
        printf "# watchdog-config.yaml — generated by install.sh on %s\n" "$(date '+%Y-%m-%d %H:%M:%S')"
        printf "# Edit and then run: docker-compose restart watchdog\n\n"

        printf "alert_channels: []  # no channels — alerts logged to stdout only\n"

        printf "\ncheck_interval_seconds:     60\n"
        printf "cooldown_minutes:            5\n"
        printf "restart_threshold:           5\n"
        printf "restart_window_minutes:      10\n"
        printf "unhealthy_cycles_threshold:  3\n"

        printf "\nexcluded_containers: []\n"

        printf "\nrunbook_base_url: \"\"\n"
        printf "\nlog_level: INFO\n"
        printf "log_file: /var/log/watchdog/watchdog.log\n"

        printf "\nsyslog:\n"
        printf "  enabled:  false\n"
        printf "  host:     127.0.0.1\n"
        printf "  port:     514\n"
        printf "  protocol: udp\n"
        printf "  facility: local0\n"

        printf "\nsmtp:\n"
        printf "  enabled:      false\n"
        printf "  host:         smtp.radware.com\n"
        printf "  port:         587\n"
        printf "  sender:       noc-alerts@radware.com\n"
        printf "  recipients:\n"
        printf "    - ops-team@radware.com\n"
        printf "    - oncall@radware.com\n"
        printf "  tls:          true\n"
        printf "  password_env: SMTP_PASSWORD\n"

        printf "\nsnmp_trap:\n"
        printf "  enabled:   false\n"
        printf "  host:      127.0.0.1\n"
        printf "  port:      162\n"
        printf "  community: public\n"
        printf "  trap_oid:  \"1.3.6.1.6.3.1.1.5.4\"\n"

        printf "\nslack:\n"
        printf "  enabled:         false\n"
        printf "  webhook_url_env: SLACK_WEBHOOK_URL\n"
    } > "$CONFIG_FILE"

    print_success "watchdog-config.yaml written with defaults: ${CONFIG_FILE}"
    print_info "Edit ${CONFIG_FILE} to configure alert channels and thresholds"
}

################################################################################
# Start Watchdog
################################################################################

start_watchdog() {
    print_section "Starting Watchdog"

    cd "${INSTALL_DIR}"

    # Stop and remove existing container if present
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_info "Stopping existing container..."
        $DC down
    fi

    print_info "Starting container with docker compose..."
    $DC up -d
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
        print_info "  $DC logs watchdog"
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
    echo "  $DC -f ${INSTALL_DIR}/docker-compose.yaml ps"
    echo ""
    echo -e "${GREEN}Restart watchdog:${NC}"
    echo "  $DC -f ${INSTALL_DIR}/docker-compose.yaml restart"
    echo ""
    echo -e "${GREEN}View stdout (Docker logs):${NC}"
    echo "  docker logs -f ${CONTAINER_NAME}"
    echo ""
    echo -e "${GREEN}Edit alert channels / thresholds:${NC}"
    echo "  nano ${INSTALL_DIR}/watchdog-config.yaml"
    echo "  $DC -f ${INSTALL_DIR}/docker-compose.yaml restart"
    echo ""
    echo -e "${GREEN}Edit secrets (Slack / SMTP):${NC}"
    echo "  nano ${INSTALL_DIR}/.env"
    echo "  $DC -f ${INSTALL_DIR}/docker-compose.yaml up -d"
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
    configure_watchdog_yaml
    start_watchdog
    verify_deployment
    display_usage_instructions
}

main "$@"
