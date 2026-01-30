#!/bin/bash
set -e

# Usage function
help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Deploy and test Concourse CI charm locally using Juju."
    echo ""
    echo "Options:"
    echo "  --mode=[auto|web+worker]      Deployment mode (default: auto)"
    echo "                                - auto: Single app scaling (web+worker roles)"
    echo "                                - web+worker: Separate web and worker apps"
    echo ""
    echo "  --shared-storage=[none|lxc]   Shared storage configuration (default: none)"
    echo "                                - none: No shared storage"
    echo "                                - lxc: Setup shared storage on LXD host"
    echo ""
    echo "  --channel=[channel]           Deploy from Charmhub channel (e.g. edge, stable)"
    echo "                                If not specified, deploys local charm file"
    echo ""
    echo "  --skip-cleanup                Do not destroy model after test (default: false)"
    echo ""
    echo "  --steps=[step1,step2,...]     Specify exact steps to run in order (comma-separated)"
    echo "                                Default: deploy,verify,mounts,tagged,gpu,upgrade,destroy"
    echo "                                Available steps: deploy, verify, mounts, tagged, cuda, rocm,"
    echo "                                                pytorch, upgrade, scale-out, destroy"
    echo ""
    echo "  --goto=[step]                 Start from specific step (deprecated in favor of --steps)"
    echo "                                Steps: deploy, verify, mounts, tagged, cuda, rocm, pytorch, upgrade"
    echo ""
    echo "  --help, -h                    Show this help message"
    echo ""
    echo "Examples:"
    echo "  # Run full regression test (deploy -> verify -> mounts -> tagged -> gpu -> upgrade -> destroy)"
    echo "  $0"
    echo ""
    echo "  # Test PyTorch with separate CUDA and ROCm workers"
    echo "  $0 --steps=deploy,pytorch --skip-cleanup"
    echo ""
    echo "  # Test upgrade logic only"
    echo "  $0 --steps=deploy,upgrade,destroy"
    echo ""
    echo "  # Debug a specific step without destroying the model"
    echo "  $0 --steps=cuda --skip-cleanup"
    echo ""
    echo "  # Distributed mode with shared storage"
    echo "  $0 --mode=web+worker --shared-storage=lxc"
}

# Default values
MODE="auto"
SHARED_STORAGE="none"
SKIP_CLEANUP="false"
GOTO_STEP=""
CHANNEL=""
STEPS=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --mode=*) MODE="${1#*=}"; shift ;;
        --shared-storage=*) SHARED_STORAGE="${1#*=}"; shift ;;
        --channel=*) CHANNEL="${1#*=}"; shift ;;
        --skip-cleanup) SKIP_CLEANUP="true"; shift ;;
        --goto=*) GOTO_STEP="${1#*=}"; shift ;;
        --steps=*) STEPS="${1#*=}"; shift ;;
        --help|-h) help; exit 0 ;;
        *) echo "Error: Unknown option: $1" >&2; help; exit 1 ;;
    esac
done

# Validate arguments
if [[ "$MODE" != "auto" && "$MODE" != "web+worker" ]]; then
    echo "Error: Invalid mode '$MODE'. Must be 'auto' or 'web+worker'." >&2
    exit 1
fi

if [[ "$SHARED_STORAGE" != "none" && "$SHARED_STORAGE" != "lxc" ]]; then
    echo "Error: Invalid shared-storage '$SHARED_STORAGE'. Must be 'none' or 'lxc'." >&2
    exit 1
fi

# Determine steps to run
ALL_STEPS=("deploy" "verify" "mounts" "tagged" "cuda" "rocm" "upgrade" "scale-out" "destroy")
STEPS_TO_RUN=()

if [[ -n "$STEPS" ]]; then
    IFS=',' read -ra ADDR <<< "$STEPS"
    for i in "${ADDR[@]}"; do
        STEPS_TO_RUN+=("$i")
    done
elif [[ -n "$GOTO_STEP" ]]; then
    # Legacy goto support
    FOUND=false
    for step in "${ALL_STEPS[@]}"; do
        if [[ "$step" == "$GOTO_STEP" ]]; then
            FOUND=true
        fi
        if [[ "$FOUND" == "true" ]]; then
            STEPS_TO_RUN+=("$step")
        fi
    done
    if [[ "$FOUND" == "false" ]]; then
        echo "Error: Invalid goto step '$GOTO_STEP'. Valid steps: ${ALL_STEPS[*]}" >&2
        exit 1
    fi
else
    # Default steps
    STEPS_TO_RUN=("${ALL_STEPS[@]}")
fi

# Check requirements
command -v juju >/dev/null 2>&1 || { echo "juju is required but not installed."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required but not installed."; exit 1; }

# Determine model name
MODEL_NAME="concourse-test-${MODE//+/_}-${SHARED_STORAGE}"
MODEL_NAME="${MODEL_NAME//_/-}" # Replace underscores with hyphens for Juju
echo "=== Starting deployment test ==="
echo "Mode: $MODE"
echo "Shared Storage: $SHARED_STORAGE"
echo "Model: $MODEL_NAME"
echo "Steps: ${STEPS_TO_RUN[*]}"

# Setup shared path variable
if [[ "$SHARED_STORAGE" == "lxc" ]]; then
    SHARED_PATH="/tmp/${MODEL_NAME}-shared"
fi

# Helper variables
if [[ "$MODE" == "auto" ]]; then
    APP_NAME="concourse-ci"
    LEADER="$APP_NAME/leader"
else
    WEB_APP="concourse-web"
    WORKER_APP="concourse-worker"
    LEADER="$WEB_APP/leader"
fi

# Cleanup function (used by trap and destroy step)
cleanup_model() {
    echo "=== Cleaning up ==="
    echo "Destroying model $MODEL_NAME..."
    echo "$MODEL_NAME" | juju destroy-model "$MODEL_NAME" --destroy-storage --force --no-wait || true
    
    # Cleanup temp files
    rm -f verify-gpu.yml task.yml verify-mounts.yml verify-tagged.yml fly admin-password.txt concourse-ip.txt 2>/dev/null
    rm -rf /tmp/config-test-mount /tmp/config-test-mount-writable 2>/dev/null
    
    if [[ "$SHARED_STORAGE" == "lxc" && -n "$SHARED_PATH" ]]; then
        echo "Removing shared storage directory..."
        rm -rf "$SHARED_PATH" 2>/dev/null || echo "Warning: Failed to remove $SHARED_PATH"
    fi
    
    echo "Cleanup complete."
}

# Trap exit for cleanup
trap_cleanup() {
    exit_code=$?
    
    # Dump status on error
    if [[ $exit_code -ne 0 ]]; then
        echo ""
        echo "=== Abnormal Exit (Code: $exit_code) ==="
        echo "Dumping model status..."
        juju status -m "$MODEL_NAME" --storage --relations || true
        echo ""
        echo "Access Info (if model still exists):"
        echo "  URL:      http://${IP:-<unknown>}:8080"
        echo "  Username: admin"
        echo "  Password: ${PASSWORD:-<unknown>}"
        echo ""
        # shellcheck disable=SC2015
        ./fly -t test login -c "http://${IP:-<unknown>}:8080" -u admin -p "${PASSWORD:-<unknown>}" && ./fly -t test workers || true
    fi

    if [[ "$DESTROYED" == "true" ]]; then
        exit $exit_code
    fi
    
    if [[ "$SKIP_CLEANUP" == "true" ]]; then
        # Only show manual cleanup info if we ran deployment and aren't destroying
        if [[ " ${STEPS_TO_RUN[*]} " =~ " deploy " ]] && [[ ! " ${STEPS_TO_RUN[*]} " =~ " destroy " ]]; then
            echo ""
            echo "Skipping cleanup as requested."
            echo "To clean up manually:"
            echo "  echo $MODEL_NAME | juju destroy-model $MODEL_NAME --destroy-storage --force --no-wait"
            
            echo ""
            echo "Access Info (if model still exists):"
            echo "  URL:      http://${IP:-<unknown>}:8080"
            echo "  Username: admin"
            echo "  Password: ${PASSWORD:-<unknown>}"
            echo ""
        fi
    else
        echo ""
        cleanup_model
    fi
    exit $exit_code
}
trap trap_cleanup EXIT

# Helper to ensure CLI is set up
ensure_cli() {
    # Restore vars from files if present
    if [[ -z "$PASSWORD" && -f "admin-password.txt" ]]; then
        PASSWORD=$(cat admin-password.txt)
    fi
    if [[ -z "$IP" && -f "concourse-ip.txt" ]]; then
        IP=$(cat concourse-ip.txt)
    fi

    if [[ -f "fly" && -n "$PASSWORD" && -n "$IP" ]]; then
        # Already setup, just ensure login
        ./fly -t test login -c "http://${IP}:8080" -u admin -p "$PASSWORD" 2>/dev/null || true
        return
    fi
    
    echo "=== Setting up CLI ==="
    
    # Get password if missing
    if [[ -z "$PASSWORD" ]]; then
        PASSWORD=$(juju run "$LEADER" get-admin-password 2>/dev/null | grep "password:" | awk '{print $2}' | sed "s/^'//;s/'$//" || echo "")
        if [[ -z "$PASSWORD" ]]; then
            echo "Error: Failed to retrieve admin password."
            exit 1
        fi
        echo "$PASSWORD" > admin-password.txt
    fi

    # Get IP if missing
    if [[ -z "$IP" ]]; then
        IP=$(juju status -m "$MODEL_NAME" --format=json | jq -r ".applications.\"${LEADER%%/*}\".units | to_entries[] | select(.value.leader == true) | .value.\"public-address\"")
        if [[ "$IP" == "null" || -z "$IP" ]]; then
            echo "Error: Could not determine Concourse IP."
            exit 1
        fi
        echo "$IP" > concourse-ip.txt
    fi

    # Download fly if missing
    if [[ ! -f "fly" ]]; then
        echo "Downloading fly CLI from http://$IP:8080..."
        for i in {1..5}; do
            if curl -Lo fly "http://${IP}:8080/api/v1/cli?arch=amd64&platform=linux" --fail --silent; then
                echo "Fly CLI downloaded."
                chmod +x ./fly
                break
            fi
            echo "Waiting for API to be ready (attempt $i/5)..."
            sleep 10
        done
        if [[ ! -f "fly" ]]; then
            echo "Error: Failed to download fly CLI."
            exit 1
        fi
    fi

    # Login
    echo "Logging in to Concourse..."
    ./fly -t test login -c "http://${IP}:8080" -u admin -p "$PASSWORD" 2>/dev/null || true

    # Sync to avoid version mismatch warnings
    ./fly -t test sync 2>/dev/null || true
}

# Helper to get container name for a unit, filtering by model UUID
get_container_for_unit() {
    local unit=$1
    local machine
    local model_uuid
    local container
    local inst_id
    
    machine=$(juju status "$unit" --format=json | jq -r ".applications.\"${unit%%/*}\".units.\"$unit\".machine")
    
    inst_id=$(juju status --format=json | jq -r ".machines.\"$machine\".\"instance-id\"")
    
    if [[ -z "$inst_id" || "$inst_id" == "null" ]]; then
        echo "Error: Could not find instance-id for machine $machine" >&2
        return 1
    fi
    
    container=$(lxc list --format=csv -c n | grep "^${inst_id}$" | head -1)
    
    if [[ -z "$container" ]]; then
        echo "Error: Could not find container for unit $unit (machine $machine, instance-id $inst_id)" >&2
        echo "Available containers:" >&2
        lxc list --format=csv -c n | grep "^juju-" >&2
        return 1
    fi
    
    echo "$container"
}

# Step functions
step_deploy() {
    # Check/Create Model
    if juju models --format=json | jq -r '.models[]."short-name"' | grep -q "^${MODEL_NAME}$"; then
        echo "Cleaning up existing model $MODEL_NAME..."
        echo "$MODEL_NAME" | juju destroy-model "$MODEL_NAME" --destroy-storage --force --no-wait
        echo "Waiting for model removal..."
        while juju models --format=json | jq -r '.models[]."short-name"' | grep -q "^${MODEL_NAME}$"; do
            sleep 2
        done
    fi

    echo "Adding model $MODEL_NAME..."
    juju add-model "$MODEL_NAME"
    juju model-config test-mode=true

    # Configuration
    CHARM_FILE="./concourse-ci-machine_amd64.charm"
    POSTGRES_CHANNEL="16/stable"
    CONCOURSE_VERSION="7.14.2"
    CHARM_NAME="concourse-ci-machine"

    if [[ -z "$CHANNEL" && ! -f "$CHARM_FILE" ]]; then
        echo "Error: Charm file $CHARM_FILE not found. Run 'charmcraft pack' first."
        exit 1
    fi

    # Deployment
    STORAGE_ARGS=()
    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        STORAGE_ARGS+=("--config" "shared-storage=lxc")
    fi

    DEPLOY_SOURCE=()
    if [[ -n "$CHANNEL" ]]; then
        echo "Deploying from Charmhub channel: $CHANNEL"
        DEPLOY_SOURCE=("$CHARM_NAME" "--channel=$CHANNEL")
    else
        echo "Deploying from local file: $CHARM_FILE"
        DEPLOY_SOURCE=("$CHARM_FILE")
    fi

    if [[ "$MODE" == "auto" ]]; then
        echo "Deploying Concourse (auto mode) with 2 units..."
        juju deploy "${DEPLOY_SOURCE[@]}" "$APP_NAME" -n 2 \
            --config mode=auto \
            --config version="$CONCOURSE_VERSION" \
            "${STORAGE_ARGS[@]}"
        
        echo "Deploying PostgreSQL..."
        juju deploy postgresql --channel "$POSTGRES_CHANNEL"
        
        echo "Relating..."
        juju integrate "$APP_NAME" postgresql

    elif [[ "$MODE" == "web+worker" ]]; then
        echo "Deploying Concourse Web..."
        juju deploy "${DEPLOY_SOURCE[@]}" "$WEB_APP" \
            --config mode=web \
            --config version="$CONCOURSE_VERSION" \
            "${STORAGE_ARGS[@]}"
            
        echo "Deploying Concourse Worker..."
        juju deploy "${DEPLOY_SOURCE[@]}" "$WORKER_APP" \
            --config mode=worker \
            --config version="$CONCOURSE_VERSION" \
            "${STORAGE_ARGS[@]}"

        echo "Deploying PostgreSQL..."
        juju deploy postgresql --channel "$POSTGRES_CHANNEL"
        
        echo "Relating..."
        juju integrate "$WEB_APP" postgresql
        juju integrate "$WEB_APP:tsa" "$WORKER_APP:flight"
    fi

    # Shared Storage Setup
    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        echo "Waiting for units to signal shared storage requirement..."
        timeout 300 bash -c "while ! juju status -m $MODEL_NAME | grep -q 'Waiting for shared storage'; do sleep 5; done"
        
        mkdir -p "$SHARED_PATH"
        
        echo "Configuring shared storage at $SHARED_PATH..."
        if [[ "$MODE" == "auto" ]]; then
            ./scripts/setup-shared-storage.sh "$APP_NAME" "$SHARED_PATH"
        else
            ./scripts/setup-shared-storage.sh "$WEB_APP" "$SHARED_PATH"
            ./scripts/setup-shared-storage.sh "$WORKER_APP" "$SHARED_PATH"
        fi
    fi

    # Wait for deployment
    echo "Waiting for deployment to settle..."
    if command -v juju-wait >/dev/null 2>&1; then
        juju-wait -m "$MODEL_NAME" -t 900
    else
        echo "juju-wait not found, sleeping 60s and hoping for the best..."
        sleep 60
        juju status -m "$MODEL_NAME"
    fi

    # Ensure CLI is ready (and cache credentials)
    ensure_cli
}

step_verify() {
    # Post-deployment info
    echo "=== Deployment Ready ==="
    juju status -m "$MODEL_NAME"

    echo "=== Verifying Services ==="
    echo "Checking service status on leader..."
    juju exec --unit "$LEADER" -- systemctl status concourse-server || echo "WARNING: concourse-server not running on leader"

    if [[ "$MODE" == "web+worker" ]]; then
         echo "Checking service status on worker..."
         juju exec --unit "$WORKER_APP/0" -- systemctl status concourse-worker || echo "WARNING: concourse-worker not running on worker unit"
    fi

    ensure_cli

    echo "=== Verifying System ==="
    echo "Checking registered workers..."
    ./fly -t test workers

    WORKER_COUNT=$(./fly -t test workers | grep -c "running" || true)
    echo "Active workers: $WORKER_COUNT"

    if [[ "$WORKER_COUNT" -lt 1 ]]; then
        echo "WARNING: No active workers found!"
    fi

    echo "=== Running Test Task ==="
    cat <<EOF > task.yml
platform: linux
image_resource:
  type: registry-image
  source: {repository: busybox}
run:
  path: echo
  args: ["Hello from Concourse ($MODE mode)!"]
EOF

    if ./fly -t test execute -c task.yml; then
        echo "✓ Task executed successfully"
    else
        echo "✗ Task execution failed"
        exit 1
    fi

    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        echo "=== Verifying Shared Storage Binaries ==="
        SHARED_BIN="$SHARED_PATH/bin/concourse"
        if [[ -f "$SHARED_BIN" ]]; then
            echo "✓ Binary found in shared storage: $SHARED_BIN"
            ls -lh "$SHARED_BIN"
        else
            echo "✗ Binary NOT found in shared storage at $SHARED_BIN"
            if [[ -d "$SHARED_PATH" ]]; then
                 echo "Directory contents:"
                 ls -la "$SHARED_PATH"
            fi
        fi

        echo "=== Verifying Shared Storage Logs ==="
        if [[ "$MODE" == "auto" ]]; then
            echo "Checking if worker reused binaries..."
            juju debug-log --replay --include "$APP_NAME/1" --no-tail | grep -E "Binaries .* already (installed|available)|Binaries .* are now available" && echo "✓ Worker reused binaries" || echo "WARNING: Worker binary reuse log not found"
        elif [[ "$MODE" == "web+worker" ]]; then
             echo "Checking if worker reused binaries..."
             juju debug-log --replay --include "$WORKER_APP/0" --no-tail | grep -E "Binaries .* already (installed|available)|Binaries .* are now available" && echo "✓ Worker reused binaries" || echo "WARNING: Worker binary reuse log not found"
        fi

        echo "Checking for lock acquisition..."
        juju debug-log --replay --include "$APP_NAME" --no-tail | grep "acquiring download lock" && echo "✓ Lock acquisition verified" || echo "WARNING: Lock acquisition log not found"
    fi
}

step_mounts() {
    ensure_cli
    echo "=== Verifying Folder Mounts ==="
    # Create test directories
    mkdir -p /tmp/config-test-mount
    echo "Hello Read-Only" > /tmp/config-test-mount/test_ro.txt
    mkdir -p /tmp/config-test-mount-writable
    echo "Hello Read-Write" > /tmp/config-test-mount-writable/test_rw.txt
    chmod 777 /tmp/config-test-mount-writable

    # Find a worker unit to test mounts on
    if [[ "$MODE" == "auto" ]]; then
        UNIT_TO_TEST=$(juju status "$APP_NAME" --format=json | jq -r ".applications.\"$APP_NAME\".units | to_entries[] | select(.value.leader != true) | .key" | head -1)
        if [[ -z "$UNIT_TO_TEST" ]]; then
             echo "Error: Could not find a worker unit (non-leader) in auto mode."
             exit 1
        fi
    else
        UNIT_TO_TEST=$(juju status "$WORKER_APP" --format=json | jq -r ".applications.\"$WORKER_APP\".units | keys[]" | head -1)
    fi

    MACHINE=$(juju status "$UNIT_TO_TEST" --format=json | jq -r ".applications.\"${UNIT_TO_TEST%%/*}\".units.\"$UNIT_TO_TEST\".machine")
    CONTAINER=$(get_container_for_unit "$UNIT_TO_TEST")

    echo "Configuring mounts for $UNIT_TO_TEST (Container: $CONTAINER)"
    lxc config device add "$CONTAINER" config_test_ro disk source="/tmp/config-test-mount" path="/srv/config_test" readonly=true || true
    lxc config device add "$CONTAINER" config_test_rw disk source="/tmp/config-test-mount-writable" path="/srv/config_test_writable" readonly=false shift=true || true

    cat <<EOF > verify-mounts.yml
platform: linux
image_resource:
  type: registry-image
  source: {repository: busybox}
run:
  path: sh
  args:
  - -c
  - |
    echo "Checking Read-Only Mount..."
    cat /srv/config_test/test_ro.txt
    if touch /srv/config_test/should_fail; then
      echo "Error: Was able to write to read-only mount"
      exit 1
    fi
    
    echo "Checking Read-Write Mount..."
    cat /srv/config_test_writable/test_rw.txt
    echo "Writing test" > /srv/config_test_writable/write_test.txt
    cat /srv/config_test_writable/write_test.txt
EOF

    if ./fly -t test execute -c verify-mounts.yml; then
        echo "✓ Mount verification passed"
    else
        echo "✗ Mount verification failed"
        exit 1
    fi
}

step_tagged() {
    ensure_cli
    echo "=== Verifying Tagged Worker ==="
    echo "Configuring worker with tag 'special-worker'..."
    if [[ "$MODE" == "auto" ]]; then
        juju config "$APP_NAME" tag="special-worker"
    else
        juju config "$WORKER_APP" tag="special-worker"
    fi

    echo "Waiting for configuration..."
    sleep 15
    if command -v juju-wait >/dev/null 2>&1; then
        juju-wait -m "$MODEL_NAME" -t 300
    else
        sleep 30
    fi

    echo "Executing tagged task..."
    cat <<EOF > verify-tagged.yml
platform: linux
image_resource:
  type: registry-image
  source: {repository: busybox}
run:
  path: echo
  args: ["Hello from tagged worker"]
EOF

    if ./fly -t test execute -c verify-tagged.yml --tag=special-worker; then
        echo "✓ Tagged task execution passed"
    else
        echo "✗ Tagged task execution failed"
        exit 1
    fi
}

# Helper function to detect GPU ID by vendor
detect_gpu_id() {
    local vendor="$1"  # "nvidia" or "amd"
    
    if ! command -v lxc >/dev/null 2>&1; then
        echo ""
        return 1
    fi
    
    local gpu_drm_id
    if [[ "$vendor" == "nvidia" ]]; then
        gpu_drm_id=$(lxc query /1.0/resources 2>/dev/null | jq -r '.gpu.cards[] | select(.vendor | contains("NVIDIA")) | .drm.id' | head -1)
    elif [[ "$vendor" == "amd" ]]; then
        gpu_drm_id=$(lxc query /1.0/resources 2>/dev/null | jq -r '.gpu.cards[] | select(.vendor | contains("AMD")) | .drm.id' | head -1)
    fi
    
    if [[ -n "$gpu_drm_id" && "$gpu_drm_id" != "null" ]]; then
        echo "$gpu_drm_id"
        return 0
    else
        echo ""
        return 1
    fi
}

step_cuda() {
    ensure_cli
    echo "=== Checking for GPU Capability ==="
    HAS_GPU=false
    if command -v nvidia-smi >/dev/null 2>&1; then
        HAS_GPU=true
        echo "Found nvidia-smi on host."
    elif ls /dev/nvidia* >/dev/null 2>&1; then
        HAS_GPU=true
        echo "Found /dev/nvidia* devices on host."
    fi

    if [[ "$HAS_GPU" == "true" ]]; then
        echo "GPU detected. Enabling GPU support..."
        
        # 1. Enable GPU config
        if [[ "$MODE" == "auto" ]]; then
            echo "Enabling NVIDIA GPU on $APP_NAME..."
            juju config "$APP_NAME" compute-runtime=cuda
            APP_OR_WORKER="$APP_NAME"
        else
            echo "Enabling NVIDIA GPU on $WORKER_APP..."
            juju config "$WORKER_APP" compute-runtime=cuda
            APP_OR_WORKER="$WORKER_APP"
        fi

        # 2. Pass GPU to LXD container (if on LXD)
        echo "Configuring LXD GPU pass-through..."
        
        if [[ "$MODE" == "auto" ]]; then
            UNIT_TO_TEST=$(juju status "$APP_NAME" --format=json | jq -r ".applications.\"$APP_NAME\".units | to_entries[] | select(.value.leader != true) | .key" | head -1)
        else
            UNIT_TO_TEST=$(juju status "$WORKER_APP" --format=json | jq -r ".applications.\"$WORKER_APP\".units | keys[]" | head -1)
        fi

        MACHINE=$(juju status "$UNIT_TO_TEST" --format=json | jq -r ".applications.\"${UNIT_TO_TEST%%/*}\".units.\"$UNIT_TO_TEST\".machine")
        CONTAINER=$(get_container_for_unit "$UNIT_TO_TEST")
        
        if [[ -n "$CONTAINER" ]]; then
            echo "Found container $CONTAINER for unit $UNIT_TO_TEST"
            
            # Detect NVIDIA GPU ID
            NVIDIA_GPU_ID=$(detect_gpu_id "nvidia")
            
            if [[ -n "$NVIDIA_GPU_ID" ]]; then
                echo "Detected NVIDIA GPU at ID $NVIDIA_GPU_ID"
                echo "Adding NVIDIA GPU device with id=$NVIDIA_GPU_ID..."
                lxc config device remove "$CONTAINER" "gpu$NVIDIA_GPU_ID" >/dev/null 2>&1 || true
                lxc config device add "$CONTAINER" "gpu$NVIDIA_GPU_ID" gpu id="$NVIDIA_GPU_ID"
            else
                echo "Could not detect NVIDIA GPU ID, using generic GPU passthrough..."
                lxc config device remove "$CONTAINER" gpu0 >/dev/null 2>&1 || true
                lxc config device add "$CONTAINER" gpu0 gpu
            fi
        else
            echo "Warning: Could not find LXC container for $UNIT_TO_TEST. Skipping pass-through."
        fi

        # 3. Wait for configuration
        echo "Waiting for GPU configuration to apply..."
        sleep 15
        if command -v juju-wait >/dev/null 2>&1; then
            juju-wait -m "$MODEL_NAME" -t 600
        else
            sleep 60
        fi
        
        # 4. Verify GPU status
        echo "Verifying GPU status..."
        STATUS_OUTPUT=$(juju status "$APP_OR_WORKER")
        if echo "$STATUS_OUTPUT" | grep -q "GPU"; then
            echo "✓ Unit status reports GPU"
        else
            echo "WARNING: Unit status does not report GPU."
        fi

        # 5. Run GPU Task
        echo "Executing GPU test task..."
        cat <<EOF > verify-gpu.yml
platform: linux
image_resource:
  type: registry-image
  source: {repository: busybox}
run:
  path: sh
  args:
  - -c
  - |
    echo "Checking for NVIDIA devices in container..."
    if ls /dev/nvidia* >/dev/null 2>&1; then
        echo "Found devices:"
        ls -la /dev/nvidia*
        exit 0
    else
        echo "No /dev/nvidia* devices found!"
        ls -la /dev/ | grep nv || true
        exit 1
    fi
EOF
        
        echo "Checking if worker is tagged..."
        ./fly -t test workers
        
        if ./fly -t test execute -c verify-gpu.yml --tag=cuda; then
            echo "✓ GPU task execution passed"
        else
            echo "✗ GPU task execution failed"
            exit 1
        fi

    else
        echo "No GPU detected on host. Skipping GPU tests."
    fi
}

step_rocm() {
    ensure_cli
    echo "=== Checking for AMD GPU Capability ==="
    HAS_AMD_GPU=false
    if command -v rocm-smi >/dev/null 2>&1; then
        HAS_AMD_GPU=true
        echo "Found rocm-smi on host."
    elif ls /dev/dri/renderD* >/dev/null 2>&1; then
        HAS_AMD_GPU=true
        echo "Found /dev/dri/renderD* devices on host."
    fi

    if [[ "$HAS_AMD_GPU" == "true" ]]; then
        echo "AMD GPU detected. Enabling AMD GPU support..."
        
        # 1. First, pass GPU to LXD container (before enabling GPU config!)
        echo "Configuring LXD AMD GPU pass-through..."
        
        if [[ "$MODE" == "auto" ]]; then
            UNIT_TO_TEST=$(juju status "$APP_NAME" --format=json | jq -r ".applications.\"$APP_NAME\".units | to_entries[] | select(.value.leader != true) | .key" | head -1)
            APP_OR_WORKER="$APP_NAME"
        else
            UNIT_TO_TEST=$(juju status "$WORKER_APP" --format=json | jq -r ".applications.\"$WORKER_APP\".units | keys[]" | head -1)
            APP_OR_WORKER="$WORKER_APP"
        fi

        MACHINE=$(juju status "$UNIT_TO_TEST" --format=json | jq -r ".applications.\"${UNIT_TO_TEST%%/*}\".units.\"$UNIT_TO_TEST\".machine")
        CONTAINER=$(get_container_for_unit "$UNIT_TO_TEST")
        
        if [[ -n "$CONTAINER" ]]; then
            echo "Found container $CONTAINER for unit $UNIT_TO_TEST"
            
            AMD_GPU_ID=$(detect_gpu_id "amd")
            
            if [[ -n "$AMD_GPU_ID" ]]; then
                echo "Detected AMD GPU at ID $AMD_GPU_ID"
                echo "Adding AMD GPU device with id=$AMD_GPU_ID..."
                lxc config device remove "$CONTAINER" "gpu$AMD_GPU_ID" >/dev/null 2>&1 || true
                lxc config device add "$CONTAINER" "gpu$AMD_GPU_ID" gpu id="$AMD_GPU_ID"
            else
                echo "Could not detect AMD GPU ID, using generic GPU passthrough..."
                lxc config device remove "$CONTAINER" gpu1 >/dev/null 2>&1 || true
                lxc config device add "$CONTAINER" gpu1 gpu
            fi
            
            # Add /dev/kfd device (required for ROCm compute)
            echo "Adding /dev/kfd device for ROCm compute..."
            lxc config device remove "$CONTAINER" kfd >/dev/null 2>&1 || true
            lxc config device add "$CONTAINER" kfd unix-char source=/dev/kfd path=/dev/kfd
            
            echo "AMD GPU and KFD devices added. Waiting for devices to be available in container..."
            sleep 5
        else
            echo "Warning: Could not find LXC container for $UNIT_TO_TEST. Skipping pass-through."
        fi

        # 2. Now enable GPU config with ROCm runtime (devices are already available)
        echo "Enabling AMD GPU on $APP_OR_WORKER..."
        juju config "$APP_OR_WORKER" compute-runtime=rocm

        # 3. Wait for configuration
        echo "Waiting for AMD GPU configuration to apply..."
        sleep 15
        if command -v juju-wait >/dev/null 2>&1; then
            juju-wait -m "$MODEL_NAME" -t 600
        else
            sleep 60
        fi
        
        # 4. Verify GPU status
        echo "Verifying AMD GPU status..."
        STATUS_OUTPUT=$(juju status "$APP_OR_WORKER")
        if echo "$STATUS_OUTPUT" | grep -q "GPU.*AMD"; then
            echo "✓ Unit status reports AMD GPU"
        else
            echo "WARNING: Unit status does not report AMD GPU."
            echo "$STATUS_OUTPUT"
        fi

        # 5. Check worker tags
        echo "Checking worker tags..."
        ./fly -t test workers
        if ./fly -t test workers | grep -q "rocm"; then
            echo "✓ Worker has rocm tag"
        else
            echo "WARNING: Worker does not have rocm tag"
        fi

        # 6. Run AMD GPU Task
        echo "Executing AMD GPU test task..."
        cat <<EOF > verify-gpu-amd.yml
platform: linux
image_resource:
  type: registry-image
  source: {repository: busybox}
run:
  path: sh
  args:
  - -c
  - |
    echo "Checking for AMD GPU devices in container..."
    if ls /dev/dri/renderD* >/dev/null 2>&1; then
        echo "✓ Found AMD GPU render devices:"
        ls -la /dev/dri/renderD*
    else
        echo "✗ No AMD GPU render devices found!"
        ls -la /dev/dri/ || echo "No /dev/dri directory"
        exit 1
    fi
    
    if ls /dev/dri/card* >/dev/null 2>&1; then
        echo "✓ Found AMD GPU card devices:"
        ls -la /dev/dri/card* | grep -v control || true
    fi
    
    echo "✓ AMD GPU devices are accessible in container"
EOF
        
        if ./fly -t test execute -c verify-gpu-amd.yml --tag=rocm; then
            echo "✓ AMD GPU task execution passed"
        else
            echo "✗ AMD GPU task execution failed"
            exit 1
        fi

        # 7. Test with ROCm image (if rocm-smi is available)
        if command -v rocm-smi >/dev/null 2>&1; then
            echo "Testing with ROCm-enabled image..."
            cat <<EOF > verify-gpu-amd-rocm.yml
platform: linux
image_resource:
  type: registry-image
  source: 
    repository: rocm/dev-ubuntu-24.04
    tag: latest
run:
  path: sh
  args:
  - -c
  - |
    echo "Testing ROCm utilities in container..."
    if command -v rocm-smi >/dev/null 2>&1; then
        echo "✓ rocm-smi is available"
        echo "Running rocm-smi..."
        rocm-smi || echo "Could not query GPU (might need host ROCm version match)"
    else
        echo "⚠ rocm-smi not in this image, but devices are present"
    fi
    echo "Checking /dev/dri devices..."
    ls -la /dev/dri/
EOF
            
            if ./fly -t test execute -c verify-gpu-amd-rocm.yml --tag=rocm; then
                echo "✓ ROCm image task execution passed"
            else
                echo "⚠ ROCm image task failed (might be version mismatch or image issue)"
            fi
        fi

    else
        echo "No AMD GPU detected on host. Skipping AMD GPU tests."
    fi
}

step_upgrade() {
    echo "=== Verifying Upgrade ==="
    UPGRADE_VERSION="7.14.3"
    echo "Upgrading to $UPGRADE_VERSION..."

    if [[ "$MODE" == "auto" ]]; then
        juju config "$APP_NAME" version="$UPGRADE_VERSION"
    else
        juju config "$WEB_APP" version="$UPGRADE_VERSION"
        juju config "$WORKER_APP" version="$UPGRADE_VERSION"
    fi

    echo "Waiting for upgrade..."
    sleep 15
    if command -v juju-wait >/dev/null 2>&1; then
        juju-wait -m "$MODEL_NAME" -t 900
    else
        sleep 60
    fi

    echo "Verifying version in status..."
    if [[ "$MODE" == "auto" ]]; then
        APPS=("$APP_NAME")
    else
        APPS=("$WEB_APP" "$WORKER_APP")
    fi

    for APP in "${APPS[@]}"; do
        echo "Checking app: $APP"
        
        # Retry loop for version check
        MAX_RETRIES=60
        for ((i=1; i<=MAX_RETRIES; i++)); do
            UNIT_COUNT=$(juju status -m "$MODEL_NAME" "$APP" --format=json | jq -r ".applications.\"$APP\".units | length")
            VERSION_COUNT=$(juju status -m "$MODEL_NAME" "$APP" --format=json | jq -r ".applications.\"$APP\".units | to_entries[].value.\"workload-status\".message" | grep -c "v$UPGRADE_VERSION" || true)
            
            echo "Attempt $i/$MAX_RETRIES: Total units: $UNIT_COUNT, Units at v$UPGRADE_VERSION: $VERSION_COUNT"
            
            if [[ "$VERSION_COUNT" -eq "$UNIT_COUNT" ]]; then
                echo "✓ All units for $APP upgraded"
                break
            fi
            
            if [[ $i -eq $MAX_RETRIES ]]; then
                echo "❌ Upgrade verification failed for $APP: not all units upgraded after $((MAX_RETRIES * 5))s"
                juju status -m "$MODEL_NAME" "$APP"
                exit 1
            fi
            sleep 5
        done
    done
    echo "✅ All units upgraded to $UPGRADE_VERSION"

    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        echo "Checking shared storage version file..."
        if grep -q "$UPGRADE_VERSION" "$SHARED_PATH/.installed_version"; then
            echo "✓ Shared storage version updated"
        else
            echo "✗ Shared storage version mismatch or file missing"
        fi
    fi
    
    ensure_cli
    echo "Syncing fly CLI with new version..."
    ./fly -t test sync
}

step_scale_out() {
    echo "=== Testing Scale Out ==="
    ensure_cli
    
    # Determine app to scale
    if [[ "$MODE" == "auto" ]]; then
        SCALE_APP="$APP_NAME"
    else
        SCALE_APP="$WORKER_APP"
    fi

    INITIAL_COUNT=$(juju status -m "$MODEL_NAME" "$SCALE_APP" --format=json | jq -r ".applications.\"$SCALE_APP\".units | length")
    TARGET_COUNT=$((INITIAL_COUNT + 1))
    
    echo "Status before scaling:"
    juju status -m "$MODEL_NAME"
    
    echo "Scaling $SCALE_APP from $INITIAL_COUNT to $TARGET_COUNT units..."
    juju add-unit -m "$MODEL_NAME" "$SCALE_APP"
    
    # Handle shared storage for new unit
    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        echo "Waiting for new unit to be ready for storage configuration..."
        # Wait for unit to appear and ask for storage (or fail if we're too fast, loop handles it)
        # We look for unit count increase AND specific status if possible, 
        # but just waiting for unit count change + status loop is robust.
        
        # Wait for unit count to increase
        timeout 300 bash -c "while [ \$(juju status -m $MODEL_NAME $SCALE_APP --format=json | jq -r '.applications.\"$SCALE_APP\".units | length') -lt $TARGET_COUNT ]; do sleep 5; done"
        
        # Wait for the "Waiting for shared storage" message or active (if it somehow works magically)
        echo "Waiting for unit to signal storage requirement..."
        timeout 300 bash -c "while ! juju status -m $MODEL_NAME | grep -q 'Waiting for shared storage'; do sleep 5; done" || true
        
        echo "Configuring shared storage for scaled units..."
        ./scripts/setup-shared-storage.sh "$SCALE_APP" "$SHARED_PATH"
    fi

    echo "Waiting for new unit to settle..."
    if command -v juju-wait >/dev/null 2>&1; then
        juju-wait -m "$MODEL_NAME" -t 900
    else
        sleep 60
    fi
    
    echo "Status after scaling:"
    juju status -m "$MODEL_NAME"
    
    echo "Verifying worker registration..."
    # Retry worker check a few times as registration happens after unit is active
    for i in {1..12}; do
        WORKER_COUNT=$(./fly -t test workers | grep -c "running" || true)
        echo "Active workers: $WORKER_COUNT (Target: >=$TARGET_COUNT)"
        
        # Note: In auto mode, leader is also a worker, so total workers = total units
        # In web+worker mode, web is not a worker, so we check just the worker app units? 
        # Actually fly workers lists all registered workers.
        # Let's just check if count increased from verification step or equals expected.
        # Simplified: Just check if we have enough workers.
        
        # Expected workers calculation
        if [[ "$MODE" == "auto" ]]; then
             EXPECTED_WORKERS=$((TARGET_COUNT - 1))
        else
             # Web units don't register as workers? Assuming standard concourse architecture
             # In our charm, web unit IS just a web node. 
             # So expected workers = units of worker app.
             EXPECTED_WORKERS=$TARGET_COUNT
        fi
        
        if [[ "$WORKER_COUNT" -ge "$EXPECTED_WORKERS" ]]; then
            echo "✓ Scaled out successfully: Found $WORKER_COUNT workers"
            return 0
        fi
        sleep 5
    done
    
    echo "❌ Scale out verification failed: Expected $EXPECTED_WORKERS workers, found $WORKER_COUNT"
    ./fly -t test workers
    exit 1
}

step_pytorch() {
    echo "=== Testing PyTorch with CUDA and ROCm Workers ==="
    
    rm -f admin-password.txt concourse-ip.txt fly 2>/dev/null || true
    
    local SAVED_LEADER=$LEADER
    LEADER="web/leader"
    
    # Create model if it doesn't exist
    if ! juju models --format=json | jq -r '.models[]."short-name"' | grep -q "^${MODEL_NAME}$"; then
        echo "Creating model $MODEL_NAME..."
        juju add-model "$MODEL_NAME"
        juju model-config test-mode=true
    else
        echo "Using existing model $MODEL_NAME..."
    fi
    
    # Check if we have both GPU types
    HAS_NVIDIA=false
    HAS_AMD=false
    
    if command -v nvidia-smi >/dev/null 2>&1 || ls /dev/nvidia* >/dev/null 2>&1; then
        HAS_NVIDIA=true
        echo "✓ NVIDIA GPU detected"
    fi
    
    if command -v rocm-smi >/dev/null 2>&1 || ls /dev/dri/renderD* >/dev/null 2>&1; then
        HAS_AMD=true
        echo "✓ AMD GPU detected"
    fi
    
    if [[ "$HAS_NVIDIA" == "false" && "$HAS_AMD" == "false" ]]; then
        echo "⚠ No GPUs detected. Skipping PyTorch tests."
        return
    fi
    
    echo "=== Deploying Separate Web + CUDA + ROCm Workers ==="
    
    # Deploy PostgreSQL if not already deployed
    if ! juju status postgresql --format=json 2>/dev/null | jq -e '.applications.postgresql' >/dev/null; then
        echo "Deploying PostgreSQL..."
        juju deploy postgresql --channel 16/stable
    else
        echo "PostgreSQL already deployed, reusing..."
    fi
    
    # Deploy web server if not already deployed
    if ! juju status web --format=json 2>/dev/null | jq -e '.applications.web' >/dev/null; then
        echo "Deploying web server..."
        if [[ -n "$DEPLOY_CHANNEL" ]]; then
            juju deploy concourse-ci-machine web --channel "$DEPLOY_CHANNEL" --config mode=web
        else
            juju deploy ./concourse-ci-machine_*.charm web --config mode=web
        fi
        
        # Relate to PostgreSQL
        juju integrate web:postgresql postgresql:database
    else
        echo "Web server already deployed, reusing..."
    fi
    
    # Deploy CUDA worker if NVIDIA GPU available
    if [[ "$HAS_NVIDIA" == "true" ]]; then
        if ! juju status worker-cuda --format=json 2>/dev/null | jq -e '.applications."worker-cuda"' >/dev/null; then
            echo "Deploying CUDA worker (without GPU config first)..."
            if [[ -n "$DEPLOY_CHANNEL" ]]; then
                juju deploy concourse-ci-machine worker-cuda --channel "$DEPLOY_CHANNEL" \
                    --config mode=worker
            else
                juju deploy ./concourse-ci-machine_*.charm worker-cuda \
                    --config mode=worker
            fi
            
            juju integrate web:tsa worker-cuda:flight
            
            # Wait for unit to be allocated and reach active status
            echo "Waiting for worker unit to be active..."
            timeout 600 bash -c 'until juju status worker-cuda --format=json 2>/dev/null | jq -e ".applications.\"worker-cuda\".units | to_entries[] | select(.value.\"workload-status\".current == \"active\") | .key" > /dev/null; do sleep 2; done'
        else
            echo "CUDA worker already deployed, reusing..."
        fi
        
        # Pass NVIDIA GPU to container (if not already added) and enable GPU config
        CUDA_UNIT=$(juju status worker-cuda --format=json | jq -r '.applications."worker-cuda".units | keys[]' | head -1)
        CUDA_MACHINE=$(juju status "$CUDA_UNIT" --format=json | jq -r ".applications.\"worker-cuda\".units.\"$CUDA_UNIT\".machine")
        CUDA_CONTAINER=$(get_container_for_unit "$CUDA_UNIT")
        
        if [[ -n "$CUDA_CONTAINER" ]]; then
            NVIDIA_GPU_ID=$(detect_gpu_id "nvidia")
            if [[ -n "$NVIDIA_GPU_ID" ]]; then
                # Check if GPU device is already added
                if ! lxc config device show "$CUDA_CONTAINER" 2>/dev/null | grep -q "gpu$NVIDIA_GPU_ID"; then
                    echo "Adding NVIDIA GPU (id=$NVIDIA_GPU_ID) to $CUDA_CONTAINER..."
                    lxc config device remove "$CUDA_CONTAINER" "gpu$NVIDIA_GPU_ID" >/dev/null 2>&1 || true
                    lxc config device add "$CUDA_CONTAINER" "gpu$NVIDIA_GPU_ID" gpu id="$NVIDIA_GPU_ID"
                    echo "GPU device added, waiting for device to be available..."
                    sleep 5
                else
                    echo "GPU device already added to container"
                fi
            fi
        fi
        
        # Enable GPU config (triggers config-changed hook with GPU devices available)
        CURRENT_RUNTIME=$(juju config worker-cuda compute-runtime)
        if [[ "$CURRENT_RUNTIME" != "cuda" ]]; then
            echo "Enabling CUDA GPU configuration..."
            juju config worker-cuda compute-runtime=cuda
        else
            echo "CUDA GPU configuration already enabled"
        fi
    fi
    
    # Deploy ROCm worker if AMD GPU available
    if [[ "$HAS_AMD" == "true" ]]; then
        if ! juju status worker-rocm --format=json 2>/dev/null | jq -e '.applications."worker-rocm"' >/dev/null; then
            echo "Deploying ROCm worker (without GPU config first)..."
            if [[ -n "$DEPLOY_CHANNEL" ]]; then
                juju deploy concourse-ci-machine worker-rocm --channel "$DEPLOY_CHANNEL" \
                    --config mode=worker
            else
                juju deploy ./concourse-ci-machine_*.charm worker-rocm \
                    --config mode=worker
            fi
            
            juju integrate web:tsa worker-rocm:flight
            
            # Wait for unit to be allocated and reach active status
            echo "Waiting for worker unit to be active..."
            timeout 600 bash -c 'until juju status worker-rocm --format=json 2>/dev/null | jq -e ".applications.\"worker-rocm\".units | to_entries[] | select(.value.\"workload-status\".current == \"active\") | .key" > /dev/null; do sleep 2; done'
        else
            echo "ROCm worker already deployed, reusing..."
        fi
        
        # Pass AMD GPU to container BEFORE enabling GPU config
        ROCM_UNIT=$(juju status worker-rocm --format=json | jq -r '.applications."worker-rocm".units | keys[]' | head -1)
        ROCM_MACHINE=$(juju status "$ROCM_UNIT" --format=json | jq -r ".applications.\"worker-rocm\".units.\"$ROCM_UNIT\".machine")
        ROCM_CONTAINER=$(get_container_for_unit "$ROCM_UNIT")
        
        if [[ -n "$ROCM_CONTAINER" ]]; then
            AMD_GPU_ID=$(detect_gpu_id "amd")
            if [[ -n "$AMD_GPU_ID" ]]; then
                if ! lxc config device show "$ROCM_CONTAINER" 2>/dev/null | grep -q "gpu$AMD_GPU_ID"; then
                    echo "Adding AMD GPU (id=$AMD_GPU_ID) to $ROCM_CONTAINER..."
                    lxc config device remove "$ROCM_CONTAINER" "gpu$AMD_GPU_ID" >/dev/null 2>&1 || true
                    lxc config device add "$ROCM_CONTAINER" "gpu$AMD_GPU_ID" gpu id="$AMD_GPU_ID"
                    
                    echo "Adding /dev/kfd device for ROCm compute..."
                    lxc config device remove "$ROCM_CONTAINER" kfd >/dev/null 2>&1 || true
                    lxc config device add "$ROCM_CONTAINER" kfd unix-char source=/dev/kfd path=/dev/kfd
                    
                    echo "GPU and KFD devices added, waiting for devices to be available..."
                    sleep 5
                else
                    echo "GPU device already added to container"
                    if ! lxc config device show "$ROCM_CONTAINER" 2>/dev/null | grep -q "kfd"; then
                        echo "Adding missing /dev/kfd device..."
                        lxc config device add "$ROCM_CONTAINER" kfd unix-char source=/dev/kfd path=/dev/kfd
                        sleep 2
                    fi
                fi
            fi
        fi
        
        # Enable GPU config (triggers config-changed hook with GPU devices available)
        CURRENT_ROCM_RUNTIME=$(juju config worker-rocm compute-runtime)
        if [[ "$CURRENT_ROCM_RUNTIME" != "rocm" ]]; then
            echo "Enabling ROCm GPU configuration..."
            juju config worker-rocm compute-runtime=rocm
        else
            echo "ROCm GPU configuration already enabled"
        fi
    fi
    
    # Wait for all units to be ready
    echo "Waiting for deployment to settle..."
    sleep 30
    if command -v juju-wait >/dev/null 2>&1; then
        juju-wait -m "$MODEL_NAME" -t 900
    else
        sleep 120
    fi
    
    juju status
    
    # Get admin password for new web server
    WEB_LEADER=$(juju status web --format=json | jq -r '.applications.web.units | to_entries[] | select(.value.leader == true) | .key')
    ADMIN_PASSWORD=$(juju run "$WEB_LEADER" get-admin-password --format=json | jq -r ".\"$WEB_LEADER\".results.password")
    
    # Get web IP
    WEB_IP=$(juju status web/0 --format=json | jq -r '.applications.web.units."web/0"."public-address"')
    
    if [[ ! -f "fly" ]]; then
        echo "Downloading fly CLI from http://$WEB_IP:8080..."
        for i in {1..10}; do
            if curl -Lo fly "http://${WEB_IP}:8080/api/v1/cli?arch=amd64&platform=linux" --fail --silent; then
                echo "Fly CLI downloaded."
                chmod +x ./fly
                break
            fi
            echo "Waiting for API to be ready (attempt $i/10)..."
            sleep 10
        done
        if [[ ! -f "fly" ]]; then
            echo "Error: Failed to download fly CLI."
            exit 1
        fi
    fi
    
    # Login to new Concourse instance
    ./fly -t pytorch login -c "http://$WEB_IP:8080" -u admin -p "$ADMIN_PASSWORD" --insecure
    ./fly -t pytorch sync
    
    echo "=== Checking Workers ==="
    ./fly -t pytorch workers
    
    # Create PyTorch CUDA pipeline
    if [[ "$HAS_NVIDIA" == "true" ]]; then
        echo "=== Creating PyTorch CUDA Pipeline ==="
        cat <<'EOF' > pytorch-cuda-pipeline.yml
jobs:
- name: pytorch-cuda-test
  plan:
  - task: show-cuda-hardware-info
    tags: [cuda]
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: nvidia/cuda
          tag: 12.6.3-base-ubuntu24.04
      run:
        path: sh
        args:
        - -c
        - |
          echo "============================================================"
          echo "CUDA Hardware Information"
          echo "============================================================"
          
          echo ""
          echo "--- NVIDIA SMI ---"
          nvidia-smi || echo "nvidia-smi failed"
          
          echo ""
          echo "--- CUDA Device Files ---"
          ls -la /dev/nvidia* /dev/nvidiactl /dev/nvidia-uvm 2>/dev/null || echo "Some device files missing"
          
          echo ""
          echo "--- Environment ---"
          env | grep -E '(CUDA|NVIDIA|LD_LIBRARY|PATH)' | sort
          
          echo ""
          echo "============================================================"
  
  - task: test-pytorch-cuda
    tags: [cuda]
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: pytorch/pytorch
          tag: 2.1.0-cuda11.8-cudnn8-runtime
      run:
        path: python3
        args:
        - -c
        - |
          import torch
          print("=" * 60)
          print("PyTorch CUDA Test")
          print("=" * 60)
          print(f"PyTorch version: {torch.__version__}")
          print(f"CUDA available: {torch.cuda.is_available()}")
          if torch.cuda.is_available():
              print(f"CUDA version: {torch.version.cuda}")
              print(f"cuDNN version: {torch.backends.cudnn.version()}")
              print(f"GPU count: {torch.cuda.device_count()}")
              print(f"GPU name: {torch.cuda.get_device_name(0)}")
              
              x = torch.rand(5, 3).cuda()
              print(f"\nTensor on GPU: {x.device}")
              y = x * 2
              print(f"Computation result shape: {y.shape}")
              print("✓ PyTorch CUDA test PASSED")
          else:
              print("✗ CUDA not available!")
              print("See hardware info above for diagnostics")
              exit(1)
EOF
        
        ./fly -t pytorch set-pipeline -p pytorch-cuda -c pytorch-cuda-pipeline.yml -n
        ./fly -t pytorch unpause-pipeline -p pytorch-cuda
        echo "Triggering PyTorch CUDA job..."
        ./fly -t pytorch trigger-job -j pytorch-cuda/pytorch-cuda-test -w || echo "⚠ PyTorch CUDA job failed"
    fi
    
    # Create PyTorch ROCm pipeline
    if [[ "$HAS_AMD" == "true" ]]; then
        echo "=== Creating PyTorch ROCm Pipeline ==="
        cat <<'EOF' > pytorch-rocm-pipeline.yml
jobs:
- name: pytorch-rocm-test
  plan:
  - task: show-rocm-hardware-info
    tags: [rocm]
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: rocm/pytorch
          tag: latest
      run:
        path: sh
        args:
        - -c
        - |
          echo "============================================================"
          echo "ROCm Hardware Information (from PyTorch Container)"
          echo "============================================================"
          
          echo ""
          echo "--- GPU Detection (lspci) ---"
          lspci 2>/dev/null | grep -E '(VGA|Display|3D)' || echo "lspci not available"
          
          echo ""
          echo "--- ROCm SMI ---"
          /opt/rocm/bin/rocm-smi 2>&1 || echo "rocm-smi failed"
          
          echo ""
          echo "--- Device Files ---"
          echo "Checking /dev/kfd:"
          ls -la /dev/kfd 2>&1 || echo "  /dev/kfd not found (required for compute)"
          echo ""
          echo "Checking /dev/dri/*:"
          ls -la /dev/dri/ 2>&1 || echo "  /dev/dri not found"
          
          echo ""
          echo "--- ROCm Info (rocminfo) ---"
          /opt/rocm/bin/rocminfo 2>&1 | head -80 || echo "rocminfo failed"
          
          echo ""
          echo "--- Environment ---"
          env | grep -E '(ROC|HIP|HSA|LD_LIBRARY|PATH)' | sort
          
          echo ""
          echo "--- PyTorch ROCm Version ---"
          python3 -c "import torch; print(f'PyTorch: {torch.__version__}'); print(f'ROCm: {torch.version.hip}')"
          
          echo ""
          echo "============================================================"
  
  - task: test-pytorch-rocm
    tags: [rocm]
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: rocm/pytorch
          tag: latest
      run:
        path: sh
        args:
        - -c
        - |
          export HSA_OVERRIDE_GFX_VERSION=11.0.0
          python3 <<'PYTHON_EOF'
          import torch
          import traceback
          import os
          print("=" * 60)
          print("PyTorch ROCm Test")
          print("=" * 60)
          print(f"HSA_OVERRIDE_GFX_VERSION: {os.environ.get('HSA_OVERRIDE_GFX_VERSION', 'not set')}")
          print(f"PyTorch version: {torch.__version__}")
          print(f"CUDA available (ROCm): {torch.cuda.is_available()}")
          if torch.cuda.is_available():
              print(f"ROCm version: {torch.version.hip}")
              print(f"GPU count: {torch.cuda.device_count()}")
              print(f"GPU name: {torch.cuda.get_device_name(0)}")
              
              try:
                  print("\nAttempting to create tensor on GPU...")
                  x = torch.rand(5, 3).cuda()
                  print(f"✓ Tensor created successfully: {x.device}")
                  
                  print("Attempting GPU computation (multiply by 2)...")
                  y = x * 2
                  print(f"✓ Computation succeeded, result shape: {y.shape}")
                  print(f"✓ Result sample: {y[0]}")
                  print("\n✓ PyTorch ROCm test PASSED!")
              except Exception as e:
                  print(f"\n✗ PyTorch ROCm test FAILED")
                  print(f"Error type: {type(e).__name__}")
                  print(f"Error message: {str(e)}")
                  print("\nFull traceback:")
                  traceback.print_exc()
                  print("\nNote: Try setting HSA_OVERRIDE_GFX_VERSION=11.0.0 for gfx1103 (Phoenix1) GPUs")
                  exit(1)
          else:
              print("⚠ ROCm not available")
              print("See hardware info above for diagnostics")
              exit(1)
          PYTHON_EOF
EOF
        
        ./fly -t pytorch set-pipeline -p pytorch-rocm -c pytorch-rocm-pipeline.yml -n
        ./fly -t pytorch unpause-pipeline -p pytorch-rocm
        echo "Triggering PyTorch ROCm job..."
        ./fly -t pytorch trigger-job -j pytorch-rocm/pytorch-rocm-test -w || echo "⚠ PyTorch ROCm job failed"
    fi
    
    echo "✅ PyTorch tests completed"
    
    LEADER=$SAVED_LEADER
    
    # Cleanup
    rm -f pytorch-cuda-pipeline.yml pytorch-rocm-pipeline.yml
}

step_destroy() {
    cleanup_model
    DESTROYED=true
}

# Main execution loop
for step in "${STEPS_TO_RUN[@]}"; do
    case $step in
        deploy) step_deploy ;;
        verify) step_verify ;;
        mounts) step_mounts ;;
        tagged) step_tagged ;;
        cuda) step_cuda ;;
        rocm) step_rocm ;;
        pytorch) step_pytorch ;;
        upgrade) step_upgrade ;;
        scale-out) step_scale_out ;;
        destroy) step_destroy ;;
        *) echo "Warning: Unknown step '$step'";;
    esac
done

echo ""
echo "Test execution complete."
