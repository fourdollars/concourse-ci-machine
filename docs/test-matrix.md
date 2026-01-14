# CI Test Matrix

This document describes the test scenarios covered in the GitHub Actions CI workflow (`.github/workflows/ci.yml`).

## Test Matrix

The following table summarizes the test coverage for different deployment modes and features in the CI pipeline.

| Feature / Test | `mode=auto` | `mode=auto` + `shared-storage=lxc` | `mode=web` + `mode=worker` | `mode=web` + `mode=worker` + `shared-storage=lxc` | `mode=all` | `mode=all` + `shared-storage=lxc` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **CI Job Name** | `test-auto-mode` | `test-shared-storage-auto` | `test-web-worker-mode` | `test-shared-storage-web-worker` | `test-all-mode` | `test-shared-storage-all` |
| **Fly Execute Task** | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| **Mounts (Bind)** | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Tagged Workers** | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Upgrade Test** | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Units Tested** | 2 | 1 → 2 | 3 | 2 | 1 | 1 → 2 |

### Matrix Key
- **Fly Execute Task**: Verifies the system by running a dummy task (e.g., `busybox` echo) via the Concourse CLI.
- **Mounts (Bind)**: Tests the ability to mount host directories into worker containers (read-only and read-write).
- **Tagged Workers**: Verifies task placement on workers with specific tags (`tag=special-worker`).
- **Upgrade Test**: Performs an in-place upgrade (e.g., `7.14.2` → `7.14.3`) and verifies stability.
- **Units Tested**: The number of units deployed in the test (arrow `→` indicates scaling during the test).

## Dependencies

- **Build Artifact**: The `build-charm` job packs the charm once, and all test jobs consume the same artifact.
- **Environment**: All tests run on `ubuntu-latest`.
- **LXD**: `5.21/stable`
- **Juju**: `3.6/stable`
- **Database**: `postgresql` from channel `16/stable`.
- **Concourse Version**: Tests explicitly verify version `7.14.2`.

## Execution Details

### Fly CLI Verification
Most test jobs include a verification step using the `fly` CLI, which is the command-line interface for Concourse.
1.  **Download**: The CLI is downloaded directly from the deployed Concourse Web node (`/api/v1/cli`).
2.  **Login**: Authenticates using the generated admin password.
3.  **Execute**: Runs a simple task (usually `busybox` based) to ensure the Concourse scheduler can successfully dispatch work to the registered workers.
    - `test-auto-mode`: Runs a simple echo task.
    - `test-shared-storage-auto`: Runs a simple echo task.
    - `test-web-worker-mode`: Runs tasks on both default and tagged (`special-worker`) workers.
    - `test-all-mode`: Runs a simple echo task.

### Mount Verification (`test-web-worker-mode`, `test-shared-storage-auto`)
This job specifically tests the ability to mount host directories into Concourse worker containers, simulating "bind mounts" often used for configuration or persistent data.

1.  **Host Preparation**:
    - Creates `/tmp/config-test-mount` (Read-Only source).
    - Creates `/tmp/config-test-mount-writable` (Read-Write source).
2.  **LXD Device Mapping**:
    - Uses `lxc config device add` to mount these directories into the worker containers.
    - **Read-Only**: Mounted to `/srv/config_test` with `readonly=true`.
    - **Read-Write**: Mounted to `/srv/config_test_writable` with `readonly=false` and `shift=true` (for UID mapping).
3.  **Task Verification**:
    - A custom `task.yml` is executed on the workers via `fly`.
    - **Read-Only Check**: Tries to `touch` a file in `/srv/config_test` and expects it to **fail**.
    - **Read-Write Check**: Tries to write to `/srv/config_test_writable` and expects it to **succeed**.

### Shared Storage Testing
Tests involving `shared-storage=lxc` simulate a production environment where storage is shared between units (e.g., via NFS, but simulated here with LXD mounts).

1. **Deployment**: Units are deployed with `--config shared-storage=lxc`.
2. **Mounting**: The `scripts/setup-shared-storage.sh` helper script is used to mount a host directory (e.g., `/tmp/concourse-shared`) into the LXD containers at `/var/lib/concourse`.
3. **Verification**: The tests check that:
   - The charm detects the mounted storage.
   - Binaries are downloaded to the shared path.
   - Subsequent units/workers reuse the existing binaries instead of re-downloading.

### Verification Steps
Tests typically include:
- `juju-wait` to ensure the model settles.
- `systemctl status` checks for `concourse-server` and `concourse-worker` services inside the units.
- Connection checks (getting admin password, verifying CLI access).
