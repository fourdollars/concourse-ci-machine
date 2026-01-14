#!/bin/bash
# Script to discover worker units and set up shared storage via LXC
# This enables efficient shared storage for Concourse installations in LXD
# Usage: ./setup-shared-storage.sh <application-name> <shared-storage-path>

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_debug() {
    echo -e "${BLUE}[DEBUG]${NC} $1"
}

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <application-name> <shared-storage-path> [container-mount-path]"
    echo ""
    echo "This script discovers worker units and sets up shared storage via LXC disk mounts."
    echo ""
    echo "Arguments:"
    echo "  application-name       Name of the Juju application (e.g., concourse, concourse-worker)"
    echo "  shared-storage-path    Host path to use as shared storage (e.g., /var/lib/concourse/shared)"
    echo "  container-mount-path   Path inside container (default: /var/lib/concourse)"
    echo ""
    echo "Examples:"
    echo "  $0 concourse /tmp/concourse-shared"
    echo "  $0 concourse-worker /var/lib/concourse/shared"
    echo "  $0 concourse /data/shared /opt/concourse-shared"
    echo ""
    echo "Note: The script will:"
    echo "  1. Find all units of the application"
    echo "  2. Identify worker and web units based on mode configuration"
    echo "  3. Set up LXC disk devices for each unit container"
    echo "  4. Create .lxc_shared_storage marker file to indicate shared storage is ready"
    exit 1
fi

APP_NAME="$1"
HOST_STORAGE_PATH="$2"
CONTAINER_MOUNT_PATH="${3:-/var/lib/concourse}"

print_info "Shared Storage Setup for Concourse Workers"
echo "=============================================="
echo "Application:      $APP_NAME"
echo "Host Path:        $HOST_STORAGE_PATH"
echo "Container Path:   $CONTAINER_MOUNT_PATH"
echo ""

# Check if required tools are available
if ! command -v juju &> /dev/null; then
    print_error "juju command not found. Please install Juju."
    exit 1
fi

if ! command -v jq &> /dev/null; then
    print_error "jq command not found. Please install jq."
    exit 1
fi

if ! command -v lxc &> /dev/null; then
    print_error "lxc command not found. Please install LXD."
    exit 1
fi

# Create shared storage directory on host if it doesn't exist
if [ ! -d "$HOST_STORAGE_PATH" ]; then
    print_info "Creating shared storage directory: $HOST_STORAGE_PATH"
    mkdir -p "$HOST_STORAGE_PATH"
    chmod 777 "$HOST_STORAGE_PATH"
    print_info "✓ Directory created with 777 permissions"
fi

# Get application status
print_info "Fetching application status..."
APP_STATUS=$(juju status "$APP_NAME" --format=json 2>/dev/null)

if [ $? -ne 0 ] || [ -z "$APP_STATUS" ]; then
    print_error "Failed to get status for application: $APP_NAME"
    print_error "Make sure the application is deployed and the name is correct."
    exit 1
fi

# Extract units
UNITS=$(echo "$APP_STATUS" | jq -r '.applications."'"$APP_NAME"'".units | keys[]' 2>/dev/null)

if [ -z "$UNITS" ]; then
    print_error "No units found for application: $APP_NAME"
    exit 1
fi

print_info "Found $(echo "$UNITS" | wc -l) unit(s)"
echo ""

# Get leader unit
LEADER_UNIT=$(echo "$APP_STATUS" | jq -r '.applications."'"$APP_NAME"'".units | to_entries[] | select(.value.leader == true) | .key' 2>/dev/null)

if [ -n "$LEADER_UNIT" ]; then
    print_debug "Leader unit: $LEADER_UNIT"
fi

# Determine which units are workers and web
WORKER_UNITS=()
WEB_UNITS=()

for UNIT in $UNITS; do
    print_info "Analyzing unit: $UNIT"
    
    # Get unit configuration
    UNIT_INFO=$(echo "$APP_STATUS" | jq -r '.applications."'"$APP_NAME"'".units."'"$UNIT"'"' 2>/dev/null)
    
    # Get machine ID
    MACHINE=$(echo "$UNIT_INFO" | jq -r '.machine' 2>/dev/null)
    
    if [ -z "$MACHINE" ] || [ "$MACHINE" = "null" ]; then
        print_warn "  Could not determine machine for $UNIT, skipping..."
        continue
    fi
    
    print_debug "  Machine ID: $MACHINE"
    
    # Get unit's mode configuration
    MODE=$(juju config "$APP_NAME" mode 2>/dev/null)
    print_debug "  Application mode: $MODE"
    
    # Determine if this unit is a worker or web
    IS_WORKER=false
    IS_WEB=false
    
    case "$MODE" in
        "worker")
            # All units in worker mode are workers
            IS_WORKER=true
            print_debug "  → Worker (mode=worker)"
            ;;
        "auto")
            # In auto mode, leader is web, non-leaders are workers
            if [ "$UNIT" = "$LEADER_UNIT" ]; then
                IS_WEB=true
                print_debug "  → Web (mode=auto, leader)"
            else
                IS_WORKER=true
                print_debug "  → Worker (mode=auto, non-leader)"
            fi
            ;;
        "all")
            # In all mode, every unit runs both web and worker
            IS_WORKER=true
            IS_WEB=true
            print_debug "  → Worker + Web (mode=all, runs both web+worker)"
            ;;
        "web")
            # Web-only units
            IS_WEB=true
            print_debug "  → Web only (mode=web)"
            ;;
        *)
            print_warn "  Unknown mode: $MODE, skipping..."
            continue
            ;;
    esac
    
    if [ "$IS_WORKER" = true ]; then
        WORKER_UNITS+=("$UNIT:$MACHINE")
        print_info "  ✓ Identified as worker unit"
    fi
    
    if [ "$IS_WEB" = true ]; then
        WEB_UNITS+=("$UNIT:$MACHINE")
        print_info "  ✓ Identified as web unit"
    fi
    
    echo ""
done

# Check if we found any workers or web units
if [ ${#WORKER_UNITS[@]} -eq 0 ] && [ ${#WEB_UNITS[@]} -eq 0 ]; then
    print_warn "No worker or web units found!"
    print_warn "Make sure:"
    print_warn "  - Application is deployed with appropriate mode"
    print_warn "  - Units are in active state"
    exit 1
fi

print_info "Found ${#WORKER_UNITS[@]} worker unit(s) and ${#WEB_UNITS[@]} web unit(s) to configure"
echo ""

# Combine all units that need storage
ALL_UNITS=("${WORKER_UNITS[@]}" "${WEB_UNITS[@]}")

# Configure LXC storage for each unit
DEVICE_NAME="shared-storage"
SUCCESS_COUNT=0
FAILED_COUNT=0

for UNIT_INFO in "${ALL_UNITS[@]}"; do
    UNIT=$(echo "$UNIT_INFO" | cut -d: -f1)
    MACHINE=$(echo "$UNIT_INFO" | cut -d: -f2)
    
    print_info "Configuring shared storage for: $UNIT"
    
    # Find LXC container by machine ID
    # Pattern: juju-<model>-<machine-id>
    CONTAINER=$(lxc list --format=csv -c n | grep "^juju-.*-${MACHINE}$" | head -1)
    
    if [ -z "$CONTAINER" ]; then
        print_error "  Container not found for machine $MACHINE"
        FAILED_COUNT=$((FAILED_COUNT + 1))
        echo ""
        continue
    fi
    
    print_debug "  Container: $CONTAINER"
    
    # Check if device already exists
    if lxc config device show "$CONTAINER" 2>/dev/null | grep -q "^${DEVICE_NAME}:"; then
        print_warn "  Shared storage device already exists. Removing old configuration..."
        lxc config device remove "$CONTAINER" "$DEVICE_NAME" 2>/dev/null || true
    fi
    
    # Add shared storage device with shift=true for automatic UID/GID mapping
    print_info "  Mounting: $HOST_STORAGE_PATH → $CONTAINER_MOUNT_PATH (with shift=true)"
    if lxc config device add "$CONTAINER" "$DEVICE_NAME" disk \
        source="$HOST_STORAGE_PATH" \
        path="$CONTAINER_MOUNT_PATH" \
        shift=true 2>&1; then
        
        print_info "  ✓ LXC device configured"
        
        # Check if container is running to verify mount
        CONTAINER_STATE=$(lxc list "$CONTAINER" --format=csv -c s 2>/dev/null)
        
        if [ "$CONTAINER_STATE" = "RUNNING" ]; then
            # Verify mount is accessible
            if lxc exec "$CONTAINER" -- test -d "$CONTAINER_MOUNT_PATH" 2>/dev/null; then
                print_info "  ✓ Mount point accessible in container"
                
                # Create marker file to indicate shared storage is ready
                MARKER_FILE="$CONTAINER_MOUNT_PATH/.lxc_shared_storage"
                if lxc exec "$CONTAINER" -- sh -c "echo 'shared-storage-ready' > '$MARKER_FILE'" 2>/dev/null; then
                    print_info "  ✓ Marker file created"
                fi
            else
                print_warn "  Could not verify mount (container running but path not accessible)"
            fi
        else
            print_debug "  Container is $CONTAINER_STATE, mount will be active when started"
        fi
        
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        print_error "  Failed to configure LXC device"
        FAILED_COUNT=$((FAILED_COUNT + 1))
    fi
    
    echo ""
done

# Create marker file in host shared storage if it doesn't exist
MARKER_FILE="$HOST_STORAGE_PATH/.lxc_shared_storage"
if [ ! -f "$MARKER_FILE" ]; then
    print_info "Creating marker file: $MARKER_FILE"
    touch "$MARKER_FILE"
    print_info "✓ Marker file created"
fi

# Summary
echo "=============================================="
print_info "Setup Summary"
echo "  Success: $SUCCESS_COUNT"
echo "  Failed:  $FAILED_COUNT"
echo ""

if [ $SUCCESS_COUNT -gt 0 ]; then
    print_info "Next steps:"
    echo "  1. Set the charm config to use shared storage:"
    echo "     juju config $APP_NAME shared-storage=lxc"
    echo ""
    echo "  2. The charm will detect the mounted storage and use it automatically"
    echo "     (it checks for $CONTAINER_MOUNT_PATH/.lxc_shared_storage file)"
    echo ""
    echo "  3. Monitor the charm logs:"
    echo "     juju debug-log --include $APP_NAME"
    echo ""
    
    print_info "To verify shared storage:"
    echo "  # Check LXC device configuration"
    echo "  lxc config device show <container-name>"
    echo ""
    echo "  # Verify mount and marker file inside container"
    echo "  juju ssh $APP_NAME/0 -- ls -la $CONTAINER_MOUNT_PATH"
    echo "  juju ssh $APP_NAME/0 -- ls -la $CONTAINER_MOUNT_PATH/.lxc_shared_storage"
fi

if [ $FAILED_COUNT -gt 0 ]; then
    echo ""
    print_warn "Some units failed to configure. Check the errors above."
    exit 1
fi

print_info "Shared storage setup complete! ✓"
