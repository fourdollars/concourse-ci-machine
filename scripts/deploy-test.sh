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
    echo "  --skip-cleanup                Do not destroy model after test (default: false)"
    echo ""
    echo "  --goto=[step]                 Start from specific step (default: deploy)"
    echo "                                Steps: deploy, verify, mounts, tagged, upgrade"
    echo ""
    echo "  --help, -h                    Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --mode=web+worker --shared-storage=lxc"
    echo "  $0 --goto=verify --skip-cleanup"
    exit 0
}

# Default values
MODE="auto"
SHARED_STORAGE="none"
SKIP_CLEANUP="false"
GOTO_STEP="deploy"

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --mode=*) MODE="${1#*=}"; shift ;;
        --shared-storage=*) SHARED_STORAGE="${1#*=}"; shift ;;
        --skip-cleanup) SKIP_CLEANUP="true"; shift ;;
        --goto=*) GOTO_STEP="${1#*=}"; shift ;;
        --help|-h) help ;;
        *) echo "Unknown option: $1"; help ;;
    esac
done

# Validate arguments
if [[ "$MODE" != "auto" && "$MODE" != "web+worker" ]]; then
    echo "Error: Invalid mode '$MODE'. Must be 'auto' or 'web+worker'."
    exit 1
fi

if [[ "$SHARED_STORAGE" != "none" && "$SHARED_STORAGE" != "lxc" ]]; then
    echo "Error: Invalid shared-storage '$SHARED_STORAGE'. Must be 'none' or 'lxc'."
    exit 1
fi

# Determine if we should run a step
should_run() {
    local step_order=("deploy" "verify" "mounts" "tagged" "upgrade")
    local current_step="$1"
    
    # If GOTO_STEP matches current_step, we start running
    if [[ "$GOTO_STEP" == "$current_step" ]]; then
        return 0
    fi
    
    # Find index of goto step and current step
    local goto_idx=-1
    local current_idx=-1
    
    for i in "${!step_order[@]}"; do
        if [[ "${step_order[$i]}" == "$GOTO_STEP" ]]; then
            goto_idx=$i
        fi
        if [[ "${step_order[$i]}" == "$current_step" ]]; then
            current_idx=$i
        fi
    done
    
    if [[ $goto_idx -eq -1 ]]; then
        echo "Error: Invalid goto step '$GOTO_STEP'. Valid steps: ${step_order[*]}"
        exit 1
    fi
    
    # Run if current step is after or equal to goto step
    if [[ $current_idx -ge $goto_idx ]]; then
        return 0
    else
        return 1
    fi
}

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
echo "Starting from step: $GOTO_STEP"

# Cleanup function
cleanup() {
    exit_code=$?
    if [[ "$SKIP_CLEANUP" == "true" ]]; then
        echo ""
        echo "Skipping cleanup as requested."
        echo "To clean up manually:"
        echo "  juju destroy-model $MODEL_NAME --destroy-storage --force --no-wait -y"
    else
        echo ""
        echo "=== Cleaning up ==="
        echo "Destroying model $MODEL_NAME..."
        echo "$MODEL_NAME" | juju destroy-model "$MODEL_NAME" --destroy-storage --force --no-wait || true
        
        # Cleanup temp files
        rm -f task.yml verify-mounts.yml verify-tagged.yml fly admin-password.txt concourse-ip.txt 2>/dev/null
        rm -rf /tmp/config-test-mount /tmp/config-test-mount-writable 2>/dev/null
        
        if [[ "$SHARED_STORAGE" == "lxc" && -n "$SHARED_PATH" ]]; then
            echo "Removing shared storage directory..."
            rm -rf "$SHARED_PATH" 2>/dev/null || echo "Warning: Failed to remove $SHARED_PATH"
        fi
        
        echo "Cleanup complete."
    fi
    exit $exit_code
}

# Trap exit for cleanup
trap cleanup EXIT

# Setup shared path variable (needed for verify steps even if skipped deploy)
if [[ "$SHARED_STORAGE" == "lxc" ]]; then
    SHARED_PATH="/tmp/${MODEL_NAME}-shared"
fi

if should_run "deploy"; then
    # Check/Create Model
    if juju models --format=json | jq -r '.models[].name' | grep -q "^${MODEL_NAME}$"; then
        echo "Cleaning up existing model $MODEL_NAME..."
        echo "$MODEL_NAME" | juju destroy-model "$MODEL_NAME" --destroy-storage --force --no-wait
        # Wait for model to disappear (simple loop)
        echo "Waiting for model removal..."
        while juju models --format=json | jq -r '.models[].name' | grep -q "^${MODEL_NAME}$"; do
            sleep 2
        done
    fi

    echo "Adding model $MODEL_NAME..."
    juju add-model "$MODEL_NAME"

    # Configuration
    CHARM_FILE="./concourse-ci-machine_amd64.charm"
    POSTGRES_CHANNEL="16/stable"
    CONCOURSE_VERSION="7.14.2"

    if [[ ! -f "$CHARM_FILE" ]]; then
        echo "Error: Charm file $CHARM_FILE not found. Run 'charmcraft pack' first."
        exit 1
    fi

    # Deployment
    STORAGE_ARGS=()
    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        STORAGE_ARGS+=("--config" "shared-storage=lxc")
    fi

    if [[ "$MODE" == "auto" ]]; then
        APP_NAME="concourse-ci"
        echo "Deploying Concourse (auto mode)..."
        juju deploy "$CHARM_FILE" "$APP_NAME" \
            --config mode=auto \
            --config version="$CONCOURSE_VERSION" \
            "${STORAGE_ARGS[@]}"
        
        echo "Deploying PostgreSQL..."
        juju deploy postgresql --channel "$POSTGRES_CHANNEL"
        
        echo "Relating..."
        juju relate "$APP_NAME:postgresql" postgresql:database

    elif [[ "$MODE" == "web+worker" ]]; then
        WEB_APP="concourse-web"
        WORKER_APP="concourse-worker"
        
        echo "Deploying Concourse Web..."
        juju deploy "$CHARM_FILE" "$WEB_APP" \
            --config mode=web \
            --config version="$CONCOURSE_VERSION" \
            "${STORAGE_ARGS[@]}"
            
        echo "Deploying Concourse Worker..."
        juju deploy "$CHARM_FILE" "$WORKER_APP" \
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
        timeout 900 bash -c "while ! juju status -m $MODEL_NAME | grep -q 'Waiting for shared storage mount'; do sleep 5; done"
        
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
else
    echo "Skipping deploy step..."
fi

# Define app names even if deploy skipped (needed for verification)
if [[ "$MODE" == "auto" ]]; then
    APP_NAME="concourse-ci"
    LEADER="$APP_NAME/leader"
else
    WEB_APP="concourse-web"
    WORKER_APP="concourse-worker"
    LEADER="$WEB_APP/leader"
fi

if should_run "verify"; then
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

    echo "=== Setting up CLI ==="
    PASSWORD=$(juju run "$LEADER" get-admin-password 2>/dev/null | grep "password:" | awk '{print $2}' || echo "N/A")
    IP=$(juju status -m "$MODEL_NAME" --format=json | jq -r ".applications.\"${LEADER%%/*}\".units | to_entries[] | select(.value.leader == true) | .value.\"public-address\"")

    if [[ "$IP" == "null" || -z "$IP" ]]; then
        echo "Error: Could not determine Concourse IP."
        exit 1
    fi

    echo "Downloading fly CLI from http://$IP:8080..."
    # Retry curl a few times as the API might take a moment to come up fully
    for i in {1..5}; do
        if curl -Lo fly "http://${IP}:8080/api/v1/cli?arch=amd64&platform=linux" --fail --silent; then
            echo "Fly CLI downloaded."
            break
        fi
        echo "Waiting for API to be ready (attempt $i/5)..."
        sleep 10
    done

    if [[ ! -f "fly" ]]; then
        echo "Error: Failed to download fly CLI."
        exit 1
    fi

    chmod +x ./fly

    echo "Logging in to Concourse..."
    ./fly -t test login -c "http://${IP}:8080" -u admin -p "$PASSWORD"

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
        # Check if binaries exist in the shared path on the host
        # (Assuming we are running on the LXD host)
        SHARED_BIN="$SHARED_PATH/bin/concourse"
        if [[ -f "$SHARED_BIN" ]]; then
            echo "✓ Binary found in shared storage: $SHARED_BIN"
            ls -lh "$SHARED_BIN"
        else
            echo "✗ Binary NOT found in shared storage at $SHARED_BIN"
            # Don't fail the script if running remotely/in VM where path isn't local, but usually this script is for local dev
            if [[ -d "$SHARED_PATH" ]]; then
                 echo "Directory contents:"
                 ls -la "$SHARED_PATH"
            fi
        fi
    fi
else
    echo "Skipping verify step..."
    # Need to setup fly and password/IP for subsequent steps if skipping verify
    PASSWORD=$(juju run "$LEADER" get-admin-password 2>/dev/null | grep "password:" | awk '{print $2}' || echo "N/A")
    IP=$(juju status -m "$MODEL_NAME" --format=json | jq -r ".applications.\"${LEADER%%/*}\".units | to_entries[] | select(.value.leader == true) | .value.\"public-address\"")
    # Assuming fly is already there or we need it? 
    # If verify skipped, we might not have fly.
    # Let's ensure fly login if we are going to run mounts/tagged/upgrade
    if [[ ! -f "fly" ]]; then
         echo "Downloading fly CLI (required for later steps)..."
         curl -Lo fly "http://${IP}:8080/api/v1/cli?arch=amd64&platform=linux" --fail --silent || true
         chmod +x ./fly 2>/dev/null || true
    fi
    ./fly -t test login -c "http://${IP}:8080" -u admin -p "$PASSWORD" 2>/dev/null || true
fi

if should_run "mounts"; then
    echo "=== Verifying Folder Mounts ==="
    # Create test directories
    mkdir -p /tmp/config-test-mount
    echo "Hello Read-Only" > /tmp/config-test-mount/test_ro.txt
    mkdir -p /tmp/config-test-mount-writable
    echo "Hello Read-Write" > /tmp/config-test-mount-writable/test_rw.txt
    chmod 777 /tmp/config-test-mount-writable

    # Find a worker unit to test mounts on
    if [[ "$MODE" == "auto" ]]; then
        UNIT_TO_TEST=$(juju status "$APP_NAME" --format=json | jq -r ".applications.\"$APP_NAME\".units | keys[]" | head -1)
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
else
    echo "Skipping mounts step..."
fi

if should_run "tagged"; then
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
else
    echo "Skipping tagged step..."
fi

if should_run "upgrade"; then
    echo "=== Verifying Upgrade ==="
    UPGRADE_VERSION="7.14.3"
    echo "Upgrading to $UPGRADE_VERSION..."

    if [[ "$MODE" == "auto" ]]; then
        juju config "$APP_NAME" version="$UPGRADE_VERSION"
        juju run "$APP_NAME/leader" upgrade version="$UPGRADE_VERSION"
    else
        juju config "$WEB_APP" version="$UPGRADE_VERSION"
        juju config "$WORKER_APP" version="$UPGRADE_VERSION"
        juju run "$WEB_APP/leader" upgrade version="$UPGRADE_VERSION"
    fi

    echo "Waiting for upgrade..."
    sleep 15
    if command -v juju-wait >/dev/null 2>&1; then
        juju-wait -m "$MODEL_NAME" -t 900
    else
        sleep 60
    fi

    echo "Verifying version in status..."
    juju status -m "$MODEL_NAME" | grep "$UPGRADE_VERSION" || echo "WARNING: Upgrade version not seen in status"

    if [[ "$SHARED_STORAGE" == "lxc" ]]; then
        echo "Checking shared storage version file..."
        if grep -q "$UPGRADE_VERSION" "$SHARED_PATH/.installed_version"; then
            echo "✓ Shared storage version updated"
        else
            echo "✗ Shared storage version mismatch or file missing"
        fi
    fi
else
    echo "Skipping upgrade step..."
fi

echo ""
echo "Access Info:"
echo "  URL:      http://$IP:8080"
echo "  Username: admin"
echo "  Password: $PASSWORD"
echo ""
