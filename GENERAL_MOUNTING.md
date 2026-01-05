# General Folder Mounting System

## Overview

The Concourse CI Machine charm now supports automatic discovery and mounting of any folders under `/srv` in worker containers. This provides a flexible system for mounting datasets, models, outputs, and other resources without requiring charm reconfiguration.

## Key Features

- **Automatic Discovery**: Any folder placed in `/srv` on the LXC container is automatically discovered and mounted
- **Read-Only by Default**: Folders are mounted read-only for data safety
- **Writable Folders**: Folders with `_writable` or `_rw` suffix are mounted with write permissions
- **GPU Compatible**: Works seamlessly with GPU workers and existing `/srv/datasets` mounting
- **No Configuration Required**: Zero-config automatic mounting on both GPU and non-GPU workers

## Folder Naming Convention

### Read-Only Folders (Default)

Any folder without a special suffix is mounted as **read-only**:

```bash
/srv/datasets         # Read-only
/srv/models           # Read-only
/srv/reference-data   # Read-only
```

### Writable Folders

Folders ending with `_writable` or `_rw` are mounted with **write permissions**:

```bash
/srv/outputs_writable  # Read-write
/srv/cache_rw          # Read-write
/srv/models_writable   # Read-write
```

## Quick Start

### 1. Create Folders on LXC Container

On the Juju machine hosting your Concourse worker:

```bash
# Find your worker container
juju status concourse-worker

# SSH to the machine
juju ssh concourse-worker/0

# Create read-only folder
sudo mkdir -p /srv/datasets
echo "Sample data" | sudo tee /srv/datasets/sample.txt

# Create writable folder
sudo mkdir -p /srv/outputs_writable
sudo chmod 777 /srv/outputs_writable
```

### 2. Add Folders to LXC Container (Recommended)

For persistent folders that survive container restarts:

```bash
# On the Juju host machine
# Identify container name (e.g., juju-abc123-0)
lxc list

# Add disk device for read-only folder
lxc config device add juju-abc123-0 datasets disk \
    source=/path/on/host/datasets \
    path=/srv/datasets \
    readonly=true

# Add disk device for writable folder
lxc config device add juju-abc123-0 outputs disk \
    source=/path/on/host/outputs \
    path=/srv/outputs_writable
```

### 3. Deploy/Restart Worker

Folders are discovered automatically when containers start:

```bash
# For new deployment
juju deploy ./concourse-ci-machine.charm concourse-worker

# For existing deployment (refresh charm)
juju refresh concourse-worker --path=./concourse-ci-machine.charm
```

### 4. Verify in Concourse Tasks

Create a test pipeline:

```yaml
jobs:
  - name: test-mounts
    plan:
      - task: verify-folders
        config:
          platform: linux
          image_resource:
            type: registry-image
            source: {repository: busybox}
          run:
            path: sh
            args:
              - -c
              - |
                echo "=== Checking /srv folders ==="
                ls -la /srv/
                
                echo "=== Reading from read-only folder ==="
                cat /srv/datasets/sample.txt
                
                echo "=== Writing to writable folder ==="
                echo "Task output" > /srv/outputs_writable/result.txt
                cat /srv/outputs_writable/result.txt
```

## Common Use Cases

### Machine Learning Workflows

```bash
# Read-only datasets
/srv/training-data        # Training datasets (read-only)
/srv/validation-data      # Validation datasets (read-only)
/srv/pretrained-models    # Pre-trained models (read-only)

# Writable outputs
/srv/model-outputs_writable   # Save trained models
/srv/logs_writable            # Training logs
/srv/checkpoints_writable     # Model checkpoints
```

### Build Pipelines

```bash
# Read-only resources
/srv/build-tools         # Build dependencies (read-only)
/srv/reference-libs      # Reference libraries (read-only)

# Writable outputs
/srv/build-cache_rw      # Build cache for faster rebuilds
/srv/artifacts_writable  # Build artifacts
```

### Data Processing

```bash
# Read-only inputs
/srv/raw-data            # Input data (read-only)
/srv/schemas             # Data schemas (read-only)

# Writable outputs
/srv/processed_writable  # Processed output data
/srv/reports_writable    # Generated reports
```

## Folder Status Monitoring

The charm automatically reports folder status:

```bash
# Check worker status
juju status concourse-worker

# Example output:
# Worker ready (GPU: 1x NVIDIA) (3 folders: 2 RO, 1 RW)
#                                  └─ 2 read-only, 1 writable folder
```

## Troubleshooting

### Folders Not Visible in Tasks

**Problem**: Folders in `/srv` on the LXC container don't appear in Concourse tasks.

**Solutions**:
1. Verify folders exist on the LXC container:
   ```bash
   juju ssh worker/0 'ls -la /srv/'
   ```

2. Check worker logs for discovery errors:
   ```bash
   juju debug-log --include=concourse-worker
   ```

3. Restart worker to trigger rediscovery:
   ```bash
   juju ssh worker/0 'sudo systemctl restart concourse-worker'
   ```

### Permission Denied Errors

**Problem**: Cannot read from or write to mounted folders.

**Solutions**:
1. For read-only folders, ensure files are readable:
   ```bash
   sudo chmod -R a+r /srv/datasets
   ```

2. For writable folders, ensure write permissions:
   ```bash
   sudo chmod -R 777 /srv/outputs_writable
   ```

3. Check folder ownership (should be accessible by concourse-worker process)

### Write Failed on Writable Folder

**Problem**: Folder ends with `_writable` but writes fail.

**Solutions**:
1. Verify folder name suffix is correct:
   ```bash
   ls -la /srv/ | grep writable
   ```

2. Check actual folder permissions:
   ```bash
   ls -ld /srv/outputs_writable
   ```

3. Ensure LXC device allows writes (if using lxc config device):
   ```bash
   lxc config device show juju-abc123-0
   ```

## Backward Compatibility

### GPU Dataset Mounting

The existing `/srv/datasets` GPU mounting mechanism continues to work unchanged:

- `/srv/datasets` is discovered like any other folder
- Mounted as read-only by default
- Existing documentation and workflows remain valid
- No changes required to existing GPU deployments

## Technical Details

### OCI Wrapper Implementation

The folder mounting system uses OCI runtime wrappers:

- **GPU Workers**: `/usr/local/bin/runc-gpu-wrapper`
  - Injects GPU environment variables
  - Discovers and mounts all `/srv` folders
  
- **Non-GPU Workers**: `/usr/local/bin/runc-wrapper`
  - Discovers and mounts all `/srv` folders

These wrappers intercept container creation and dynamically inject bind mounts before the container starts.

### Discovery Process

1. Worker starts Concourse task
2. OCI wrapper intercepts `runc create` command
3. Wrapper scans `/srv` for directories
4. For each directory:
   - Check name suffix (`_writable`, `_rw`)
   - Determine mount options (read-only or read-write)
   - Inject bind mount into container config
5. Container starts with all mounts available

### Performance

- Discovery overhead: Minimal (<100ms for typical setups)
- Tested with 10+ concurrent folders
- No impact on task execution time

## Security Considerations

### Read-Only by Default

Folders are read-only by default to prevent accidental data corruption:

- Tasks cannot modify input data
- Immutable datasets for reproducible experiments
- Explicit opt-in required for write access (suffix naming)

### Path Validation

The wrapper validates folder paths to prevent security issues:

- Only directories under `/srv` are scanned
- Symbolic links are ignored
- Hidden files (`.something`) are skipped
- Path traversal attempts are prevented

## Advanced Configuration

### Custom Mount Paths

While `/srv` is the default scan location, you can add folders anywhere in the LXC container by using LXC disk devices with custom paths. The automatic discovery only scans `/srv`.

### Multiple Workers

Each worker independently discovers folders from its own `/srv` directory:

- Worker A: `/srv/datasets-gpu` (GPU worker)
- Worker B: `/srv/datasets-cpu` (CPU worker)
- No shared state between workers

### Dynamic Folder Addition

To add folders after worker deployment:

1. Add folder to LXC container
2. Restart Concourse worker service:
   ```bash
   juju ssh worker/0 'sudo systemctl restart concourse-worker'
   ```

New folders will be discovered on the next task execution.

## See Also

- [Dataset Mounting Guide](DATASET_MOUNTING.md) - GPU-specific dataset mounting
- [Quick Start Guide](specs/001-general-mount/quickstart.md) - Deployment walkthroughs
- [GPU Support Documentation](GPU_SUPPORT.md) - GPU worker configuration
