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
    echo "                                Additional steps: scale-out"
    echo ""
    echo "  --goto=[step]                 Start from specific step (deprecated in favor of --steps)"
    echo "                                Steps: deploy, verify, mounts, tagged, gpu, upgrade"
    echo ""
    echo "  --help, -h                    Show this help message"
    echo ""
    echo "Examples:"
    echo "  # Run full regression test (deploy -> verify -> mounts -> tagged -> gpu -> upgrade -> destroy)"
    echo "  $0"
    echo ""
    echo "  # Test upgrade logic only"
    echo "  $0 --steps=deploy,upgrade,destroy"
    echo ""
    echo "  # Debug a specific step without destroying the model"
    echo "  $0 --steps=gpu --skip-cleanup"
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
ALL_STEPS=("deploy" "verify" "mounts" "tagged" "gpu" "upgrade" "scale-out" "destroy")
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
    if [[ "$DESTROYED" == "true" ]]; then
        exit $exit_code
    fi
    
    if [[ "$SKIP_CLEANUP" == "true" ]]; then
        echo ""
        echo "Skipping cleanup as requested."
        echo "To clean up manually:"
        echo "  juju destroy-model $MODEL_NAME --destroy-storage --force --no-wait -y"
    else
        echo ""
        cleanup_model
    fi
    exit $exit_code
}
trap trap_cleanup EXIT

# Helper to ensure CLI is set up
ensure_cli() {
    if [[ -f "fly" && -n "$PASSWORD" && -n "$IP" ]]; then
        return
    fi
    
    echo "=== Setting up CLI ==="
    
    # Get password if missing
    if [[ -z "$PASSWORD" ]]; then
        PASSWORD=$(juju run "$LEADER" get-admin-password 2>/dev/null | grep "password:" | awk '{print $2}' || echo "")
        if [[ -z "$PASSWORD" ]]; then
            echo "Error: Failed to retrieve admin password."
            exit 1
        fi
    fi

    # Get IP if missing
    if [[ -z "$IP" ]]; then
        IP=$(juju status -m "$MODEL_NAME" --format=json | jq -r ".applications.\"${LEADER%%/*}\".units | to_entries[] | select(.value.leader == true) | .value.\"public-address\"")
        if [[ "$IP" == "null" || -z "$IP" ]]; then
            echo "Error: Could not determine Concourse IP."
            exit 1
        fi
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
        juju relate "$APP_NAME:postgresql" postgresql:database

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
        juju relate "$WEB_APP:postgresql" postgresql:database
        juju relate "$WEB_APP:web-tsa" "$WORKER_APP:worker-tsa"
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
    CONTAINER=$(lxc list --format=csv -c n | grep "^juju-.*-${MACHINE}$" | head -1)

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
    fi
}

step_gpu() {
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
            echo "Enabling gpu on $APP_NAME..."
            juju config "$APP_NAME" enable-gpu=true
            APP_OR_WORKER="$APP_NAME"
        else
            echo "Enabling gpu on $WORKER_APP..."
            juju config "$WORKER_APP" enable-gpu=true
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
        CONTAINER=$(lxc list --format=csv -c n | grep "^juju-.*-${MACHINE}$" | head -1)
        
        if [[ -n "$CONTAINER" ]]; then
            echo "Found container $CONTAINER for unit $UNIT_TO_TEST"
            echo "Adding GPU device..."
            lxc config device remove "$CONTAINER" gpu0 >/dev/null 2>&1 || true
            lxc config device add "$CONTAINER" gpu0 gpu
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
        
        if ./fly -t test execute -c verify-gpu.yml --tag=gpu; then
            echo "✓ GPU task execution passed"
        else
            echo "✗ GPU task execution failed"
            exit 1
        fi

    else
        echo "No GPU detected on host. Skipping GPU tests."
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
        UNIT_COUNT=$(juju status -m "$MODEL_NAME" "$APP" --format=json | jq -r ".applications.\"$APP\".units | length")
        VERSION_COUNT=$(juju status -m "$MODEL_NAME" "$APP" --format=json | jq -r ".applications.\"$APP\".units | to_entries[].value.\"workload-status\".message" | grep -c "v$UPGRADE_VERSION" || true)
        
        echo "Total units: $UNIT_COUNT, Units at v$UPGRADE_VERSION: $VERSION_COUNT"
        
        if [[ "$VERSION_COUNT" -ne "$UNIT_COUNT" ]]; then
            echo "❌ Upgrade verification failed for $APP: not all units upgraded"
            juju status -m "$MODEL_NAME" "$APP"
            exit 1
        fi
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
             EXPECTED_WORKERS=$TARGET_COUNT
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
        gpu) step_gpu ;;
        upgrade) step_upgrade ;;
        scale-out) step_scale_out ;;
        destroy) step_destroy ;;
        *) echo "Warning: Unknown step '$step'";;
    esac
done

echo ""
echo "Test execution complete."
echo "Access Info (if model still exists):"
echo "  URL:      http://$IP:8080"
echo "  Username: admin"
echo "  Password: $PASSWORD"
echo ""
