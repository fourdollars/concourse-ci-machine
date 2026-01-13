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

- **`src/charm.py`**: The main entry point. Handles Juju events (`install`, `config-changed`, `start`, relations). Orchestrates the overall lifecycle and mode selection.
- **`lib/`**: Contains modular logic to keep `charm.py` clean.
    - **`concourse_common.py`**: Shared utilities (user creation, directory setup, key generation).
    - **`concourse_web.py`**: Logic specific to the Web component (systemd service, config generation, DB connection).
    - **`concourse_worker.py`**: Logic specific to the Worker component (containerd setup, GPU configuration, TSA connection).
    - **`concourse_installer.py`**: Handles downloading and installing Concourse binaries.
    - **`folder_mount_manager.py`**: Manages the discovery and mounting of host folders into worker containers.
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

### Cleanup
```bash
# Remove the applications and their attached storage (forceful)
echo y | juju remove-application concourse --destroy-storage --no-wait --force
echo y | juju remove-application postgresql --destroy-storage --no-wait --force
```

### Debugging
- **Logs**:
    - Charm logs: `juju debug-log --include concourse`
    - Unit logs: `juju ssh concourse/0 -- cat /var/log/concourse-ci.log`
    - Service logs: `juju ssh concourse/0 -- sudo journalctl -u concourse-server` (or `concourse-worker`)
- **SSH Access**: `juju ssh concourse/0`

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
