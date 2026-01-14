# AGENTS.md - Concourse CI Machine Charm

This file contains essential information for agentic coding assistants working on the Concourse CI Machine Charm project.

## Project Overview

This is a **Juju Machine Charm** for deploying [Concourse CI](https://concourse-ci.org/), a continuous thing-doer. It is designed to run on bare metal, VMs, or LXD containers (not Kubernetes).

The charm supports flexible deployment architectures:
- **Auto-Scaling (`mode=auto`)**: The default mode. The leader unit becomes the Web node, and all other units become Workers. This allows for easy scaling by just adding units.
- **Monolith (`mode=all`)**: Web and Worker on the same unit.
- **Distributed (`mode=web` + `mode=worker`)**: Separate Web and Worker units.

### Core Architecture

- **Web Node**: Hosting the ATC (Air Traffic Control), API, and UI. Requires PostgreSQL.
- **Worker Node**: executing the build workloads. Connects to the Web node via SSH (TSA).
- **TSA (Transportation Security Administration)**: The component in the Web node that Workers register with.
- **PostgreSQL**: External database required by the Web node (connected via `postgresql` relation). **Note: Only PostgreSQL 16/stable is currently supported.**

### Future Plans (Planned Refactoring)
- Rename `web:web-tsa` relation to `web:tsa`.
- Rename `worker:worker-tsa` relation to `worker:aircraft`.
- Remove `mode=all` and replace it with `mode=auto` for single-unit (`n=1`) deployments.
- Remove `mode=web` and replace it with `mode=auto` on the leader unit.

## File Organization & Responsibilities

- **`src/charm.py`**: The main entry point. Handles Juju events (`install`, `config-changed`, `start`, `update-status`, relations). Orchestrates the overall lifecycle and mode selection.
    - **Key hooks to understand**:
        - `_on_install()`: Initial setup, downloads binaries, creates systemd services
        - `_on_config_changed()`: Responds to configuration changes, can trigger upgrades
        - `_on_update_status()`: Runs periodically (10s in test mode, 5m normally), detects if shared storage became available after install
        - `_update_status()`: Called by update-status hook, checks service health and updates unit status
- **`lib/`**: Contains modular logic to keep `charm.py` clean.
    - **`concourse_common.py`**: Shared utilities (user creation, directory setup, key generation).
    - **`concourse_web.py`**: Logic specific to the Web component (systemd service, config generation, DB connection).
    - **`concourse_worker.py`**: Logic specific to the Worker component (containerd setup, GPU configuration, TSA connection).
        - `initialize_shared_storage()`: Sets up StorageCoordinator for shared storage mode
        - `update_config()`: Creates worker-config.env file at the correct path (shared or local). **Crucial**: Injects `PATH` to prioritize `/opt/bin` for the runc wrapper.
        - `install_folder_mount_wrapper()`: Backs up original `runc` and symlinks `/var/lib/concourse/bin/runc` to the wrapper to force its usage.
    - **`concourse_installer.py`**: Handles downloading and installing Concourse binaries.
    - **`folder_mount_manager.py`**: Manages the discovery and mounting of host folders into worker containers.
    - **`storage_coordinator.py`**: Manages shared storage coordination between web and worker units.
- **`scripts/`**: Helper scripts for deployment and management.
    - **`setup-shared-storage.sh`**: Configures LXC shared storage for units (run before or after deployment).
- **`hooks/`**:
    - **`runc-wrapper`**: Intercepts OCI runtime calls to inject host folder mounts into the container's `config.json`.
- **`specs/`**: Contains design specifications for features. **Always check here before starting complex tasks.**
- **`metadata.yaml`**: Defines the charm's relations, storage, and containers.
- **`config.yaml`**: Defines the configuration options available to the user.

## Development Workflow

1.  **Understand the Goal**: Read the issue or prompt carefully.
2.  **Check for Specs**: Look in `specs/` for existing plans or create a new one if the feature is complex.
3.  **Implement**: Modify the code in `src/` and `lib/`. Follow the Code Style Guidelines below.
4.  **Build**: Use `charmcraft pack` to build the `.charm` file.
5.  **Test**: Deploy the charm locally using LXD and Juju to verify functionality.

## Build/Lint/Test Commands

### Build
```bash
# Build the charm package
charmcraft pack
```

### Deploy & Test (Local LXD)
```bash
# 1. Bootstrap a test controller (if not exists)
# Note: --config test-mode=true sets update-status-hook-interval to 10s (vs 5m) for faster feedback
juju bootstrap localhost test-controller --config test-mode=true

# 2. Deploy PostgreSQL (REQUIRED: 16/stable)
juju add-model concourse-test
juju deploy postgresql --channel 16/stable

# 3. Deploy Concourse (Monolith Mode)
juju deploy ./concourse-ci-machine_amd64.charm concourse --config mode=all

# 3. Alternative: Deploy Concourse (Auto-Scaling Mode)
juju deploy ./concourse-ci-machine_amd64.charm concourse --config mode=auto -n 3

# 3. Alternative: Deploy Concourse (Distributed Mode)
juju deploy ./concourse-ci-machine_amd64.charm concourse --config mode=web
juju deploy ./concourse-ci-machine_amd64.charm concourse-worker --config mode=worker -n 2
juju integrate concourse:web-tsa concourse-worker:worker-tsa

# 4. Integrate Database
juju integrate concourse postgresql

# 5. Monitor Status
juju status --relations --storage --watch 5s
```

### Shared Storage Setup (LXC Mode)
```bash
# For distributed mode (web + workers), shared storage enables efficient binary sharing
# The setup-shared-storage.sh script must be run AFTER units are deployed

# 1. Deploy with shared-storage=lxc config
juju deploy ./concourse-ci-machine_amd64.charm concourse-web --config mode=web --config shared-storage=lxc
juju deploy ./concourse-ci-machine_amd64.charm concourse-worker --config mode=worker --config shared-storage=lxc -n 2
juju integrate concourse-web:web-tsa concourse-worker:worker-tsa
juju integrate concourse-web postgresql

# 2. Wait for units to start (they will show "Waiting for shared storage mount")
juju status

# 3. Create shared storage directory on host
mkdir -p /tmp/concourse-shared

# 4. Run setup script for each application (web and workers must share same path)
./scripts/setup-shared-storage.sh concourse-web /tmp/concourse-shared
./scripts/setup-shared-storage.sh concourse-worker /tmp/concourse-shared

# 5. Units will automatically detect storage and complete setup (within 10s in test mode)
juju status

# Note: The script uses shift=true for automatic UID/GID mapping between host and containers
# This prevents permission denied errors when the charm writes configuration files

# To add workers dynamically:
juju add-unit concourse-worker
# Wait for unit to start, then run:
./scripts/setup-shared-storage.sh concourse-worker /tmp/concourse-shared
# The update-status hook will automatically complete the worker setup
```

### Cleanup
```bash
# Remove the applications and their attached storage (forceful)
echo y | juju remove-application concourse --destroy-storage --no-wait --force
echo y | juju remove-application postgresql --destroy-storage --no-wait --force
```

### Debugging
- **Logs**:
    - Charm logs: `juju debug-log --include concourse`
    - Charm logs for specific unit: `juju debug-log --include concourse-worker/2 --replay --no-tail`
    - Unit logs: `juju ssh concourse/0 -- cat /var/log/concourse-ci.log`
    - Service logs: `juju ssh concourse/0 -- journalctl -u concourse-server -n 50` (or `concourse-worker`)
- **SSH Access**: `juju ssh concourse/0`
- **Common Issues**:
    - "Waiting for shared storage mount": Run `./scripts/setup-shared-storage.sh` script
    - "Permission denied" in shared storage: Ensure script uses `shift=true` for UID/GID mapping
    - Worker not starting after dynamic addition: Check if worker-config.env exists, wait for update-status hook (10s in test mode)

### Linting & Formatting
```bash
# Install dependencies
uv tool install ruff
uv tool install black
uv tool install mypy

# Run checks
ruff check .
black .
mypy --ignore-missing-imports .
```

## Code Style Guidelines

### Python Standards
- **PEP 8 Compliance**: All Python code MUST follow PEP 8.
- **Type Hints**: Mandatory for all function signatures.
- **Docstrings**: Required for all public modules, classes, and functions.
- **Imports**: Sorted (Standard lib -> Third party -> Local).

### Error Handling
- **Explicit Exceptions**: Catch specific exceptions (e.g., `subprocess.CalledProcessError`) rather than bare `Exception`.
- **Status Reporting**: Update `self.unit.status` to reflect errors (e.g., `BlockedStatus("Database missing")`).

### Juju Best Practices
- **Idempotency**: Hooks can run multiple times. Ensure your code handles this (e.g., check if a file exists before writing).
- **Secrets**: Use Juju Secrets for sensitive data. NEVER log credentials.
- **Logging**: Log interesting events at `INFO`. Debugging details at `DEBUG`. Errors at `ERROR`.
- **Hook Execution Order**: Understand that `install` runs first, then `config-changed`, then `start`. The `update-status` hook runs periodically.
- **Dynamic Configuration**: If a resource (like shared storage) becomes available after the install hook, use `update-status` to detect and complete setup.

## Important Implementation Notes

### Shared Storage (LXC Mode)
- When `shared-storage=lxc` is configured, units expect `/var/lib/concourse` to be mounted via LXC disk device
- The `setup-shared-storage.sh` script must use `shift=true` parameter for automatic UID/GID mapping
- Workers added after initial deployment will:
    1. Install hook runs → detects no storage → sets "Waiting for shared storage mount"
    2. Admin runs setup script to mount storage
    3. Update-status hook detects storage is available → completes worker configuration automatically
- The update-status hook must call `worker_helper.update_config()` when shared storage becomes available to create the worker-config.env file

### Folder Mounting (Mounts)
- **Mechanism**: The charm uses a custom `runc-wrapper` to inject bind mounts from the host into Concourse task containers.
- **Implementation**:
    1.  The wrapper is installed to `/opt/bin/runc-wrapper`.
    2.  The original `runc` (system or Concourse-bundled) is backed up to `/opt/bin/runc.real`.
    3.  `/var/lib/concourse/bin/runc` is replaced with a symlink to the wrapper.
    4.  `worker-config.env` sets `PATH=/opt/bin:...` to ensure the wrapper is found first.
- **Workflow**:
    - User configures an LXC device for the unit (e.g., `lxc config device add ...`).
    - Wrapper detects mounts in `/srv/*` on the host.
    - Wrapper modifies the OCI `config.json` to bind mount these folders into the container before executing the real `runc`.
