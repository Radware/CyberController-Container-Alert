#!/usr/bin/env bash

################################################################################
# CyberController Container Watchdog - Uninstallation Script
# Version: 1.0.0
#
# Stops and removes the watchdog Docker container and image.
# Log files are preserved by default.
#
# Usage:
#   sudo bash uninstall.sh [OPTIONS]
#
# Options:
#   --keep-image    Preserve the watchdog:latest Docker image (skip image removal)
#   --keep-logs     Preserve log files (default behaviour)
#   --remove-all    Full removal: container + image + log files
#   --force         Skip confirmation prompts
#   -h, --help      Show this help
################################################################################

# Errors are handled explicitly throughout this script

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
INSTALL_DIR="$SCRIPT_DIR"
CONTAINER_NAME="watchdog"
IMAGE_NAME="watchdog:latest"
VERSION="1.0.0"
DC=""  # compose command — detected at runtime

# ── Flags ─────────────────────────────────────────────────────────────────────
KEEP_IMAGE=false
KEEP_LOGS=false
REMOVE_ALL=false
FORCE=false

# ── Runtime state (set during removal operations, read by summary) ─────────────
IMAGE_KEPT=false

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  ${RED}CyberController Container Watchdog - Uninstallation${NC}            ${BLUE}║${NC}"
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

usage() {
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Uninstall the CyberController Container Watchdog."
    echo ""
    echo "OPTIONS:"
    echo "    --keep-image    Preserve the watchdog:latest Docker image (skip image removal)"
    echo "    --keep-logs     Remove container and image; preserve log files"
    echo "    --remove-all    Full removal: container + image + log directory"
    echo "    --force         Skip confirmation prompts"
    echo "    -h, --help      Display this help message"
    echo ""
    echo "EXAMPLES:"
    echo "    # Standard uninstall (container removed; prompted for image and logs):"
    echo "    sudo bash uninstall.sh"
    echo ""
    echo "    # Remove everything without prompts:"
    echo "    sudo bash uninstall.sh --remove-all --force"
    echo ""
    echo "    # Remove container, keep image and logs:"
    echo "    sudo bash uninstall.sh --keep-image --keep-logs"
    echo ""
    echo "    # Remove container and image, keep logs:"
    echo "    sudo bash uninstall.sh --keep-logs"
    echo ""
    echo "ENVIRONMENT:"
    echo "    FORCE_COLOR=1   Force color output even when terminal does not report support"
    echo ""
    exit 0
}

################################################################################
# Parse Arguments
################################################################################

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --keep-image)   KEEP_IMAGE=true;  shift ;;
            --keep-logs)    KEEP_LOGS=true;   shift ;;
            --remove-all)   REMOVE_ALL=true;  shift ;;
            --force)        FORCE=true;       shift ;;
            -h|--help)      usage ;;
            *)
                print_error "Unknown option: $1"
                usage
                ;;
        esac
    done

    if [ "$KEEP_LOGS" = true ] && [ "$REMOVE_ALL" = true ]; then
        print_error "Cannot specify both --keep-logs and --remove-all"
        exit 1
    fi
    if [ "$KEEP_IMAGE" = true ] && [ "$REMOVE_ALL" = true ]; then
        print_error "Cannot specify both --keep-image and --remove-all"
        exit 1
    fi
}

################################################################################
# Pre-flight
################################################################################

check_docker() {
    if ! command -v docker &>/dev/null; then
        print_warning "Docker not found — skipping Docker cleanup steps"
        return 1
    fi
    return 0
}

################################################################################
# Find Installation
################################################################################

find_installation_directory() {
    print_section "Finding Installation"

    # Prefer the directory where this script lives (it is the install dir)
    if [ -f "${INSTALL_DIR}/docker-compose.yaml" ]; then
        print_success "Installation directory: ${INSTALL_DIR}"
        return 0
    fi

    # Fallback: detect from container volume mounts
    if check_docker && docker container inspect "${CONTAINER_NAME}" &>/dev/null; then
        MOUNT=$(docker inspect "${CONTAINER_NAME}" \
            --format '{{range .Mounts}}{{if eq .Destination "/var/log/watchdog"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || echo "")
        if [ -n "$MOUNT" ]; then
            INSTALL_DIR="$(dirname "$MOUNT")"
            print_info "Detected installation directory from container mounts: ${INSTALL_DIR}"
            return 0
        fi
    fi

    print_warning "Could not auto-detect installation directory — using script location: ${INSTALL_DIR}"
}

################################################################################
# Removal Plan
################################################################################

display_removal_plan() {
    print_section "Uninstallation Plan"

    echo -e "${YELLOW}The following items will be removed:${NC}"
    echo ""

    CONTAINER_EXISTS=false
    IMAGE_EXISTS=false
    LOGS_EXIST=false

    if check_docker; then
        if docker container inspect "${CONTAINER_NAME}" &>/dev/null; then
            CONTAINER_EXISTS=true
            CSTATUS=$(docker ps -a --filter "name=^${CONTAINER_NAME}$" --format '{{.Status}}')
            echo -e "  ${RED}✗${NC} Docker container : ${CONTAINER_NAME} (${CSTATUS})"
        else
            echo -e "  ${GREEN}○${NC} Docker container : ${CONTAINER_NAME} (not found)"
        fi

        if docker image inspect "${IMAGE_NAME}" &>/dev/null; then
            IMAGE_EXISTS=true
            ISIZE=$(docker image inspect "${IMAGE_NAME}" --format '{{.Size}}' | \
                    awk '{printf "%.0f MB", $1/1024/1024}')
            if [ "$KEEP_IMAGE" = true ]; then
                echo -e "  ${GREEN}✓${NC} Docker image     : ${IMAGE_NAME} (~${ISIZE}) — ${GREEN}KEEPING${NC}"
            elif [ "$REMOVE_ALL" = true ] || [ "$FORCE" = true ]; then
                echo -e "  ${RED}✗${NC} Docker image     : ${IMAGE_NAME} (~${ISIZE})"
            else
                echo -e "  ${YELLOW}?${NC} Docker image     : ${IMAGE_NAME} (~${ISIZE}) — ${YELLOW}WILL PROMPT${NC}"
            fi
        else
            echo -e "  ${GREEN}○${NC} Docker image     : ${IMAGE_NAME} (not found)"
        fi
    fi

    LOG_DIR="${INSTALL_DIR}/watchdog"
    if [ -d "$LOG_DIR" ]; then
        LOGS_EXIST=true
        LSIZE=$(du -sh "$LOG_DIR" 2>/dev/null | cut -f1 || echo "unknown")
        if [ "$KEEP_LOGS" = true ]; then
            echo -e "  ${GREEN}✓${NC} Log directory    : ${LOG_DIR} (${LSIZE}) — ${GREEN}KEEPING${NC}"
        elif [ "$REMOVE_ALL" = true ]; then
            echo -e "  ${RED}✗${NC} Log directory    : ${LOG_DIR} (${LSIZE})"
        else
            echo -e "  ${YELLOW}?${NC} Log directory    : ${LOG_DIR} (${LSIZE}) — ${YELLOW}WILL PROMPT${NC}"
        fi
    else
        echo -e "  ${GREEN}○${NC} Log directory    : ${LOG_DIR} (not found)"
    fi

    echo ""

    if [ "$CONTAINER_EXISTS" = false ] && [ "$IMAGE_EXISTS" = false ] && [ "$LOGS_EXIST" = false ]; then
        print_info "Nothing to uninstall — watchdog not found"
        exit 0
    fi
}

################################################################################
# Confirmation
################################################################################

confirm_removal() {
    if [ "$FORCE" = true ]; then
        print_warning "Force mode — skipping confirmation"
        return 0
    fi
    echo ""
    read -p "Proceed with uninstallation? [y/N]: " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        print_info "Uninstallation cancelled"
        exit 0
    fi
    echo ""
}

################################################################################
# Removal Operations
################################################################################

stop_and_remove_container() {
    print_section "Removing Container"

    if ! check_docker; then
        print_warning "Docker not available — skipping"
        return 0
    fi

    # Detect compose command (v2 plugin preferred, fall back to v1 standalone)
    if docker compose version &>/dev/null 2>&1; then
        DC="docker compose"
    elif command -v docker-compose &>/dev/null; then
        DC="docker-compose"
    else
        DC=""
    fi

    if docker container inspect "${CONTAINER_NAME}" &>/dev/null; then
        cd "${INSTALL_DIR}"
        print_info "Stopping and removing container via docker compose..."
        if [ -n "$DC" ]; then
            $DC down
        else
            docker stop "${CONTAINER_NAME}" 2>/dev/null || true
            docker rm   "${CONTAINER_NAME}" 2>/dev/null || true
        fi
        print_success "Container stopped and removed: ${CONTAINER_NAME}"
    else
        print_info "Container not found: ${CONTAINER_NAME}"
    fi
}

remove_docker_image() {
    print_section "Docker Image"

    if ! check_docker; then
        print_warning "Docker not available — skipping"
        return 0
    fi

    if ! docker image inspect "${IMAGE_NAME}" &>/dev/null; then
        print_info "Docker image not found: ${IMAGE_NAME}"
        return 0
    fi

    if [ "$KEEP_IMAGE" = true ]; then
        IMAGE_KEPT=true
        print_success "Keeping Docker image (--keep-image): ${IMAGE_NAME}"
        return 0
    fi

    if [ "$REMOVE_ALL" = true ] || [ "$FORCE" = true ]; then
        _do_remove_image
        return 0
    fi

    # Interactive prompt
    ISIZE=$(docker image inspect "${IMAGE_NAME}" --format '{{.Size}}' | \
            awk '{printf "%.0f MB", $1/1024/1024}')
    echo ""
    print_warning "Docker image: ${IMAGE_NAME} (~${ISIZE})"
    echo ""
    read -p "  Remove Docker image? [y/N]: " REMOVE_IMAGE_ANSWER
    if [[ "$REMOVE_IMAGE_ANSWER" =~ ^[Yy]$ ]]; then
        _do_remove_image
    else
        IMAGE_KEPT=true
        print_success "Docker image preserved: ${IMAGE_NAME}"
        print_info "Remove manually later with: docker rmi ${IMAGE_NAME}"
    fi
}

# Internal helper — stops/removes ALL containers that reference the image, then removes the image.
_do_remove_image() {
    print_info "Removing Docker image: ${IMAGE_NAME}..."

    # Stop and remove every container (running or stopped) that uses this image.
    # We iterate rather than rely on --filter ancestor because Docker 20.10 combos are unreliable.
    ALL_CTRS=$(docker ps -aq --filter "ancestor=${IMAGE_NAME}" 2>/dev/null || true)
    if [ -n "$ALL_CTRS" ]; then
        for CID in $ALL_CTRS; do
            CSTATUS=$(docker inspect --format '{{.State.Status}}' "$CID" 2>/dev/null || true)
            CNAME=$(docker inspect --format '{{.Name}}' "$CID" 2>/dev/null | sed 's|^/||' || true)
            if [ "$CSTATUS" = "running" ] || [ "$CSTATUS" = "paused" ]; then
                print_info "  Stopping running container ${CNAME:-$CID} (${CSTATUS})..."
                docker stop "$CID" 2>/dev/null || true
            fi
            print_info "  Removing container ${CNAME:-$CID}..."
            docker rm "$CID" 2>/dev/null || true
        done
    fi

    if docker rmi "${IMAGE_NAME}" 2>&1; then
        print_success "Docker image removed: ${IMAGE_NAME}"
    else
        print_warning "Could not remove image — check for containers using it: docker ps -a --filter ancestor=${IMAGE_NAME}"
    fi
}

remove_logs() {
    print_section "Log Files"

    LOG_DIR="${INSTALL_DIR}/watchdog"

    if [ ! -d "$LOG_DIR" ]; then
        print_info "Log directory not found: ${LOG_DIR}"
        return 0
    fi

    if [ "$KEEP_LOGS" = true ]; then
        print_success "Keeping log directory (--keep-logs): ${LOG_DIR}"
        return 0
    fi

    if [ "$REMOVE_ALL" = true ] || [ "$FORCE" = true ]; then
        rm -rf "$LOG_DIR"
        print_success "Log directory removed: ${LOG_DIR}"
        return 0
    fi

    # Interactive prompt
    LSIZE=$(du -sh "$LOG_DIR" 2>/dev/null | cut -f1 || echo "unknown")
    LCOUNT=$(find "$LOG_DIR" -type f 2>/dev/null | wc -l || echo "0")
    echo ""
    print_warning "Log directory contains ${LCOUNT} file(s) (${LSIZE}): ${LOG_DIR}"
    echo ""
    read -p "  Remove log files? [y/N]: " REMOVE_LOGS_ANSWER
    if [[ "$REMOVE_LOGS_ANSWER" =~ ^[Yy]$ ]]; then
        rm -rf "$LOG_DIR"
        print_success "Log directory removed: ${LOG_DIR}"
    else
        print_success "Log directory preserved: ${LOG_DIR}"
        print_info "Remove manually later with: rm -rf ${LOG_DIR}"
    fi
}

################################################################################
# Summary
################################################################################

display_completion_summary() {
    print_section "Uninstallation Complete"

    echo -e "${GREEN}✓ Uninstallation completed!${NC}"
    echo ""
    echo -e "${BLUE}═══ Summary ═══${NC}"
    echo ""

    if check_docker; then
        if docker container inspect "${CONTAINER_NAME}" &>/dev/null; then
            echo -e "  ${YELLOW}⚠${NC}  Container : ${CONTAINER_NAME} — ${YELLOW}STILL EXISTS${NC}"
        elif [ "$CONTAINER_EXISTS" = true ]; then
            echo -e "  ${GREEN}✓${NC}  Container : ${CONTAINER_NAME} — removed"
        else
            echo -e "  ${GREEN}○${NC}  Container : ${CONTAINER_NAME} — not found (nothing to remove)"
        fi

        if docker image inspect "${IMAGE_NAME}" &>/dev/null; then
            if [ "$IMAGE_KEPT" = true ]; then
                echo -e "  ${GREEN}✓${NC}  Image     : ${IMAGE_NAME} — ${GREEN}PRESERVED${NC}"
            else
                echo -e "  ${YELLOW}⚠${NC}  Image     : ${IMAGE_NAME} — ${YELLOW}STILL EXISTS (removal may have failed)${NC}"
            fi
        elif [ "$IMAGE_EXISTS" = true ]; then
            echo -e "  ${GREEN}✓${NC}  Image     : ${IMAGE_NAME} — removed"
        else
            echo -e "  ${GREEN}○${NC}  Image     : ${IMAGE_NAME} — not found (nothing to remove)"
        fi
    fi

    LOG_DIR="${INSTALL_DIR}/watchdog"
    if [ -d "$LOG_DIR" ]; then
        echo -e "  ${GREEN}✓${NC}  Logs      : ${LOG_DIR} — ${GREEN}PRESERVED${NC}"
        print_info "Remove manually with: rm -rf ${LOG_DIR}"
    elif [ "$LOGS_EXIST" = true ]; then
        echo -e "  ${GREEN}✓${NC}  Logs      : removed"
    else
        echo -e "  ${GREEN}○${NC}  Logs      : not found (nothing to remove)"
    fi

    echo ""
}

################################################################################
# Main
################################################################################

main() {
    parse_arguments "$@"
    print_header
    find_installation_directory
    display_removal_plan
    confirm_removal
    stop_and_remove_container
    remove_docker_image
    remove_logs
    display_completion_summary
}

main "$@"
