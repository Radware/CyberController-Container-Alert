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
# bash install.sh
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
CONTAINER_NAME="docker-container-watchdog"
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
# Prompt Utilities
################################################################################

_prompt() {
    # Usage: val=$(_prompt "Label" "default")
    local label="$1" default="$2" val
    [ -n "$default" ] \
        && printf "    %s [%s]: " "$label" "$default" >&2 \
        || printf "    %s: " "$label" >&2
    IFS= read -r val
    printf "%s" "${val:-$default}"
}

_prompt_secret() {
    # Usage: val=$(_prompt_secret "Label")  —  input is hidden
    local label="$1" val
    printf "    %s: " "$label" >&2
    IFS= read -rs val
    printf "\n" >&2
    printf "%s" "$val"
}

_prompt_yn() {
    # Usage: result=$(_prompt_yn "Label" "yes|no")  →  prints "true" or "false"
    local label="$1" default="${2:-yes}" val
    printf "    %s [%s]: " "$label" "$default" >&2
    IFS= read -r val
    val="${val:-$default}"
    [[ "$val" =~ ^[Yy] ]] && printf "true" || printf "false"
}

_in_array() {
    # Usage: _in_array "needle" "${array[@]}"
    local needle="$1"; shift
    for item in "$@"; do [ "$item" = "$needle" ] && return 0; done
    return 1
}

################################################################################
# Interactive Configuration Wizard
################################################################################

configure_all() {
    print_section "Configuration Wizard"

    ENV_FILE="${INSTALL_DIR}/.env"
    CONFIG_FILE="${INSTALL_DIR}/watchdog-config.yaml"

    # Offer to reconfigure if files already exist
    if [ -f "$ENV_FILE" ] || [ -f "$CONFIG_FILE" ]; then
        local existing=""
        [ -f "$ENV_FILE" ]    && existing+=" .env"
        [ -f "$CONFIG_FILE" ] && existing+=" watchdog-config.yaml"
        print_info "Existing configuration detected:${existing}"
        printf "  Reconfigure from scratch? [y/N]: "
        local RECONFIG
        IFS= read -r RECONFIG
        if [[ ! "$RECONFIG" =~ ^[Yy]$ ]]; then
            print_info "Keeping existing configuration"
            return 0
        fi
        echo ""
    fi

    # ── Channel selection ─────────────────────────────────────────────────────
    echo -e "  ${BLUE}Select alert channels${NC} (space-separated numbers, at least one required):"
    echo ""
    echo "    1) Slack"
    echo "    2) SMTP  (email)"
    echo "    3) SNMP Trap"
    echo "    4) Syslog"
    echo ""

    local SELECTED_CHANNELS=()
    while [ ${#SELECTED_CHANNELS[@]} -eq 0 ]; do
        printf "  Channels [1]: "
        local RAW_SELECTION
        IFS= read -r RAW_SELECTION
        RAW_SELECTION="${RAW_SELECTION:-1}"
        for num in $RAW_SELECTION; do
            case "$num" in
                1) SELECTED_CHANNELS+=("slack")     ;;
                2) SELECTED_CHANNELS+=("smtp")      ;;
                3) SELECTED_CHANNELS+=("snmp_trap") ;;
                4) SELECTED_CHANNELS+=("syslog")    ;;
                *) print_warning "  Unknown choice: $num — ignored" ;;
            esac
        done
        [ ${#SELECTED_CHANNELS[@]} -eq 0 ] && print_error "Select at least one channel"
    done
    local CHANNELS_LABEL
    CHANNELS_LABEL=$(printf ", %s" "${SELECTED_CHANNELS[@]}")
    print_success "Channels: ${CHANNELS_LABEL:2}"
    echo ""

    # ── Slack ─────────────────────────────────────────────────────────────────
    local SLACK_WEBHOOK="" SLACK_ENABLED="false"
    if _in_array "slack" "${SELECTED_CHANNELS[@]}"; then
        SLACK_ENABLED="true"
        print_info "Slack"
        while true; do
            SLACK_WEBHOOK=$(_prompt_secret "Webhook URL (https://hooks.slack.com/...)")
            [[ "$SLACK_WEBHOOK" == https://hooks.slack.com/* ]] && break
            print_error "  Must start with https://hooks.slack.com/"
        done
        echo ""
    fi

    # ── SMTP ──────────────────────────────────────────────────────────────────
    local SMTP_HOST="smtp.radware.com" SMTP_PORT="587"
    local SMTP_SENDER="noc-alerts@radware.com" SMTP_RECIPIENTS="ops-team@radware.com"
    local SMTP_TLS="true" SMTP_USERNAME="" SMTP_PASSWORD="" SMTP_ENABLED="false"
    if _in_array "smtp" "${SELECTED_CHANNELS[@]}"; then
        SMTP_ENABLED="true"
        print_info "SMTP (email)"
        SMTP_HOST=$(_prompt       "Server host"                  "smtp.radware.com")
        SMTP_PORT=$(_prompt       "Port"                         "587")
        SMTP_SENDER=$(_prompt     "Sender address"               "noc-alerts@radware.com")
        SMTP_RECIPIENTS=$(_prompt "Recipients (comma-separated)" "ops-team@radware.com")
        SMTP_TLS=$(_prompt_yn     "Use TLS?"                     "yes")
        SMTP_USERNAME=$(_prompt   "SMTP username / login email"    "")
        SMTP_PASSWORD=$(_prompt_secret "SMTP password / key value")
        echo ""
    fi

    # ── SNMP Trap ─────────────────────────────────────────────────────────────
    local SNMP_HOST="" SNMP_PORT="162" SNMP_COMMUNITY="public"
    local SNMP_ENABLED="false"
    local SNMP_VERSION="v2c" SNMP_V3_USER="" SNMP_V3_AUTH_PROTO="SHA"
    local SNMP_V3_AUTH_KEY="" SNMP_V3_PRIV_PROTO="AES" SNMP_V3_PRIV_KEY=""
    if _in_array "snmp_trap" "${SELECTED_CHANNELS[@]}"; then
        SNMP_ENABLED="true"
        print_info "SNMP Trap"
        SNMP_HOST=$(_prompt    "Receiver host / IP"            "")
        SNMP_PORT=$(_prompt    "Receiver port"                 "162")
        SNMP_VERSION=$(_prompt "SNMP version (v1 / v2c / v3)" "v2c")
        if [ "${SNMP_VERSION,,}" = "v3" ]; then
            SNMP_V3_USER=$(_prompt       "USM username"                   "")
            SNMP_V3_AUTH_PROTO=$(_prompt "Auth protocol (MD5 / SHA)"      "SHA")
            SNMP_V3_AUTH_KEY=$(_prompt_secret "Auth passphrase (empty = noAuthNoPriv)")
            SNMP_V3_PRIV_PROTO=$(_prompt "Priv protocol (DES / AES)"      "AES")
            SNMP_V3_PRIV_KEY=$(_prompt_secret "Priv passphrase (empty = authNoPriv)")
        else
            SNMP_COMMUNITY=$(_prompt "Community string"                   "public")
        fi
        echo ""
    fi

    # ── Syslog ────────────────────────────────────────────────────────────────
    local SYSLOG_HOST="" SYSLOG_PORT="514" SYSLOG_PROTO="udp"
    local SYSLOG_FACILITY="local0" SYSLOG_ENABLED="false"
    if _in_array "syslog" "${SELECTED_CHANNELS[@]}"; then
        SYSLOG_ENABLED="true"
        print_info "Syslog"
        SYSLOG_HOST=$(_prompt    "Server host / IP"    "")
        SYSLOG_PORT=$(_prompt    "Port"                "514")
        SYSLOG_PROTO=$(_prompt   "Protocol (udp/tcp)"  "udp")
        SYSLOG_FACILITY=$(_prompt "Facility"           "local0")
        echo ""
    fi

    # ── Hostname shown in alerts ──────────────────────────────────────────────
    print_info "Identity"
    local HOST_DEFAULT WATCHDOG_HOST
    HOST_DEFAULT=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "")
    WATCHDOG_HOST=$(_prompt "Hostname to display in alerts" "$HOST_DEFAULT")
    echo ""

    # ── Write .env ────────────────────────────────────────────────────────────
    {
        printf "# .env — generated by install.sh on %s\n" "$(date '+%Y-%m-%d %H:%M:%S')"
        printf "# Secrets only. All other settings are in watchdog-config.yaml.\n\n"
        if [ "$SLACK_ENABLED" = "true" ]; then
            printf "# Slack\nSLACK_WEBHOOK_URL=%s\n\n" "$SLACK_WEBHOOK"
        fi
        if [ "$SMTP_ENABLED" = "true" ]; then
            printf "# SMTP\nSMTP_USERNAME=%s\nSMTP_PASSWORD=%s\n\n" "$SMTP_USERNAME" "$SMTP_PASSWORD"
        fi
        if [ "$SNMP_ENABLED" = "true" ] && [ "${SNMP_VERSION,,}" = "v3" ]; then
            printf "# SNMP v3\nSNMP_V3_AUTH_KEY=%s\nSNMP_V3_PRIV_KEY=%s\n\n" \
                   "$SNMP_V3_AUTH_KEY" "$SNMP_V3_PRIV_KEY"
        fi
        printf "# Alert identity\nWATCHDOG_HOST=%s\n\n" "$WATCHDOG_HOST"
        printf "# Tuning\nLOG_LEVEL=INFO\n"
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    print_success ".env written: ${ENV_FILE}"

    # ── Build alert_channels YAML list ────────────────────────────────────────
    local CHANNELS_YAML=""
    for ch in "${SELECTED_CHANNELS[@]}"; do
        CHANNELS_YAML+="  - ${ch}"$'\n'
    done

    # ── Convert comma-separated SMTP recipients to YAML list ─────────────────
    local RECIPIENTS_YAML=""
    IFS=',' read -ra RECIP_ARRAY <<< "$SMTP_RECIPIENTS"
    for r in "${RECIP_ARRAY[@]}"; do
        r=$(echo "$r" | tr -d '[:space:]')
        [ -n "$r" ] && RECIPIENTS_YAML+="    - ${r}"$'\n'
    done
    [ -z "$RECIPIENTS_YAML" ] && RECIPIENTS_YAML="    - ops-team@radware.com"$'\n'

    # ── Write watchdog-config.yaml ────────────────────────────────────────────
    {
        printf "# watchdog-config.yaml — generated by install.sh on %s\n" "$(date '+%Y-%m-%d %H:%M:%S')"
        printf "# Edit values, then restart: %s -f %s/docker-compose.yaml restart\n\n" \
               "$DC" "$INSTALL_DIR"
        printf "alert_channels:\n%s" "$CHANNELS_YAML"
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
        printf "  enabled:  %s\n" "$SYSLOG_ENABLED"
        printf "  host:     %s\n" "${SYSLOG_HOST:-127.0.0.1}"
        printf "  port:     %s\n" "$SYSLOG_PORT"
        printf "  protocol: %s\n" "$SYSLOG_PROTO"
        printf "  facility: %s\n" "$SYSLOG_FACILITY"
        printf "\nsmtp:\n"
        printf "  enabled:      %s\n" "$SMTP_ENABLED"
        printf "  host:         %s\n" "$SMTP_HOST"
        printf "  port:         %s\n" "$SMTP_PORT"
        printf "  sender:       %s\n" "$SMTP_SENDER"
        printf "  username_env: SMTP_USERNAME\n"
        printf "  recipients:\n%s" "$RECIPIENTS_YAML"
        printf "  tls:          %s\n" "$SMTP_TLS"
        printf "  password_env: SMTP_PASSWORD\n"
        printf "\nsnmp_trap:\n"
        printf "  enabled:   %s\n" "$SNMP_ENABLED"
        printf "  host:      %s\n" "${SNMP_HOST:-127.0.0.1}"
        printf "  port:      %s\n" "$SNMP_PORT"
        printf "  version:   %s\n" "${SNMP_VERSION:-v2c}"
        printf "  community: %s\n" "$SNMP_COMMUNITY"
        if [ "${SNMP_VERSION,,}" = "v3" ]; then
            printf "  v3_username:      %s\n" "$SNMP_V3_USER"
            printf "  v3_auth_protocol: %s\n" "${SNMP_V3_AUTH_PROTO:-SHA}"
            printf "  v3_auth_key_env:  SNMP_V3_AUTH_KEY\n"
            printf "  v3_priv_protocol: %s\n" "${SNMP_V3_PRIV_PROTO:-AES}"
            printf "  v3_priv_key_env:  SNMP_V3_PRIV_KEY\n"
        fi
        printf "\nslack:\n"
        printf "  enabled:         %s\n" "$SLACK_ENABLED"
        printf "  webhook_url_env: SLACK_WEBHOOK_URL\n"
        printf "\nauto_health_check:\n"
        printf "  enabled: true\n"
        printf "  paths:\n"
        printf "    - /-/healthy\n"
        printf "    - /health\n"
        printf "    - /healthz\n"
        printf "    - /api/health\n"
        printf "    - /metrics\n"
        printf "    - /\n"
        printf "  timeout_seconds: 2\n"
        printf "\ncontainer_health_checks: {}\n"
    } > "$CONFIG_FILE"
    print_success "watchdog-config.yaml written: ${CONFIG_FILE}"
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
    configure_all
    start_watchdog
    verify_deployment
    display_usage_instructions
}

main "$@"
