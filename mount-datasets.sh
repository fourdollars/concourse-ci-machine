#!/bin/bash
# Helper script to mount datasets into Concourse GPU worker LXC containers
# Usage: ./mount-datasets.sh <application-name> <dataset-source-path>

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
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

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <application-name> <dataset-source-path> [mount-point]"
    echo ""
    echo "Examples:"
    echo "  $0 gpu-worker /home/user/ml-datasets"
    echo "  $0 gpu-worker /data/imagenet /srv/imagenet"
    echo ""
    echo "Default mount point: /srv/datasets"
    exit 1
fi

APP_NAME="$1"
DATASET_SOURCE="$2"
MOUNT_POINT="${3:-/srv/datasets}"

# Validate source directory exists
if [ ! -d "$DATASET_SOURCE" ]; then
    print_error "Source directory does not exist: $DATASET_SOURCE"
    exit 1
fi

print_info "Dataset Mounting Helper for Concourse GPU Workers"
echo "=================================================="
echo "Application:  $APP_NAME"
echo "Source:       $DATASET_SOURCE"
echo "Mount Point:  $MOUNT_POINT"
echo ""

# Check if juju is available
if ! command -v juju &> /dev/null; then
    print_error "juju command not found. Please install Juju."
    exit 1
fi

# Check if lxc is available
if ! command -v lxc &> /dev/null; then
    print_error "lxc command not found. Please install LXD."
    exit 1
fi

# Get application units
print_info "Fetching application units..."
UNITS=$(juju status $APP_NAME --format=json 2>/dev/null | jq -r '.applications."'$APP_NAME'".units | keys[]' 2>/dev/null)

if [ -z "$UNITS" ]; then
    print_error "No units found for application: $APP_NAME"
    print_error "Make sure the application is deployed and the name is correct."
    exit 1
fi

print_info "Found units: $(echo $UNITS | tr '\n' ' ')"
echo ""

# Process each unit
for UNIT in $UNITS; do
    print_info "Processing unit: $UNIT"
    
    # Get machine ID
    MACHINE=$(juju status $UNIT --format=json 2>/dev/null | jq -r '.applications."'$APP_NAME'".units."'$UNIT'".machine' 2>/dev/null)
    
    if [ -z "$MACHINE" ] || [ "$MACHINE" = "null" ]; then
        print_warn "Could not determine machine for $UNIT, skipping..."
        continue
    fi
    
    print_info "  Machine ID: $MACHINE"
    
    # Find container by machine ID (LXD uses different naming)
    # Pattern: juju-<something>-<machine-id>
    CONTAINER=$(lxc list --format=csv -c n | grep "^juju-.*-${MACHINE}$" | head -1)
    
    if [ -z "$CONTAINER" ]; then
        print_warn "  Container not found for machine $MACHINE, skipping..."
        continue
    fi
    
    # Check if device already exists
    DEVICE_NAME="datasets"
    if lxc config device show $CONTAINER 2>/dev/null | grep -q "^${DEVICE_NAME}:"; then
        print_warn "  Dataset device already exists. Removing old configuration..."
        lxc config device remove $CONTAINER $DEVICE_NAME 2>/dev/null || true
    fi
    
    # Add dataset device
    print_info "  Mounting $DATASET_SOURCE -> $MOUNT_POINT"
    if lxc config device add $CONTAINER $DEVICE_NAME disk \
        source="$DATASET_SOURCE" \
        path="$MOUNT_POINT" \
        readonly=true 2>&1; then
        
        print_info "  ✓ Mount successful"
        
        # Verify mount
        if lxc exec $CONTAINER -- test -d "$MOUNT_POINT" 2>/dev/null; then
            CONTENTS=$(lxc exec $CONTAINER -- ls -1 "$MOUNT_POINT" 2>/dev/null | wc -l)
            print_info "  ✓ Verified: $CONTENTS items in $MOUNT_POINT"
        else
            print_warn "  Mount point exists but may not be accessible"
        fi
    else
        print_error "  Failed to mount dataset"
    fi
    
    echo ""
done

print_info "Dataset mounting complete!"
echo ""
print_info "IMPORTANT: Restart the worker service to ensure changes are recognized:"
echo ""
for UNIT in $UNITS; do
    MACHINE=$(juju status $UNIT --format=json 2>/dev/null | jq -r '.applications."'$APP_NAME'".units."'$UNIT'".machine' 2>/dev/null)
    if [ -n "$MACHINE" ] && [ "$MACHINE" != "null" ]; then
        CONTAINER=$(lxc list --format=csv -c n | grep "^juju-.*-${MACHINE}$" | head -1)
        if [ -n "$CONTAINER" ]; then
            echo "  lxc exec $CONTAINER -- systemctl restart concourse-worker"
        fi
    fi
done
echo ""
print_info "After restart, datasets will be automatically available in GPU tasks at: $MOUNT_POINT"
print_info "No pipeline modifications required!"
echo ""
print_info "To verify, run a test task:"
echo "  fly -t local execute -c - --tag gpu <<EOF"
echo "  platform: linux"
echo "  image_resource:"
echo "    type: registry-image"
echo "    source: {repository: ubuntu, tag: latest}"
echo "  run:"
echo "    path: sh"
echo "    args: [-c, 'ls -lah $MOUNT_POINT']"
echo "  EOF"
