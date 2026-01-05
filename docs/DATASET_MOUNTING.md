# Dataset Mounting Guide for GPU Workers

## Overview

This guide explains how to mount datasets into Concourse GPU worker tasks using LXC disk devices. The charm automatically makes datasets available to task containers via OCI runtime injection.

> **Note**: This guide focuses on GPU-specific dataset mounting. For general folder mounting (including writable folders, multiple paths, and non-GPU workers), see the [General Folder Mounting Guide](GENERAL_MOUNTING.md).

## How It Works

The GPU worker charm includes an OCI runtime wrapper (`runc-gpu-wrapper`) that automatically discovers and injects **all folders under `/srv`** into every task container. The `/srv/datasets` folder is treated like any other folder in the automatic discovery system—mounted as read-only by default.

### Architecture

```
Host Machine
  └── /path/to/datasets/
       │
       ├─ LXC Device Mount ──> LXC Container
       │                        └── /srv/datasets/
       │                              │
       └─────────────────────────────┼─ OCI Wrapper Discovery & Injection
                                      │   (Automatic for ALL /srv folders)
                                      │
                                      └─> Task Container
                                          └── /srv/datasets/ (read-only)
```

### General Mounting System

As of charm revision 38+, the mounting system has been enhanced to support:

- **Automatic discovery** of any folder under `/srv` (not just `/srv/datasets`)
- **Read-only by default** for data safety
- **Writable folders** using `_writable` or `_rw` suffix
- **Works on both GPU and non-GPU workers**

**For non-dataset use cases**, see [GENERAL_MOUNTING.md](GENERAL_MOUNTING.md) for:
- Mounting multiple folders (models, outputs, caches, etc.)
- Creating writable folders for task outputs
- Using the system on non-GPU workers

## Prerequisites

- GPU-enabled Concourse worker deployed with this charm
- LXC/LXD environment (Juju localhost cloud)
- Dataset directory on the host machine

## Setup Steps

### 1. Deploy GPU Worker

```bash
# Deploy GPU worker with the charm
juju deploy concourse-ci-machine gpu-worker \
  --config deployment-mode=worker \
  --config enable-gpu=true
```

### 2. Identify the LXC Container

```bash
# Find the container name for your GPU worker
juju status gpu-worker

# The machine ID will be something like "4"
# The LXC container name will be: juju-<model-id>-<machine-id>
# Example: juju-e16396-4
```

### 3. Mount Dataset Directory into LXC Container

```bash
# Add a disk device to the LXC container
lxc config device add <container-name> datasets disk \
  source=/path/to/your/datasets \
  path=/srv/datasets \
  readonly=true

# Example:
lxc config device add juju-e16396-4 datasets disk \
  source=/home/user/ml-datasets \
  path=/srv/datasets \
  readonly=true
```

### 4. Verify Mount

```bash
# Check the device is configured
lxc config device show <container-name>

# Verify mount inside container
lxc exec <container-name> -- ls -lah /srv/datasets/

# Expected output: Your dataset files should be visible
```

### 5. No Additional Configuration Required!

The charm's OCI wrapper automatically detects `/srv/datasets` and injects it into every GPU task container. No pipeline changes needed!

## Usage in Pipelines

### Direct Access (Containerd Tasks)

Tasks tagged with `[gpu]` automatically have `/srv/datasets` mounted:

```yaml
jobs:
  - name: train-model
    plan:
      - task: training
        tags: [gpu]  # GPU workers automatically get /srv/datasets
        config:
          platform: linux
          image_resource:
            type: registry-image
            source:
              repository: pytorch/pytorch
              tag: latest
          run:
            path: python
            args:
              - -c
              - |
                # Dataset is automatically available
                import os
                print(f"Datasets: {os.listdir('/srv/datasets')}")
                
                # Read data
                with open('/srv/datasets/training-data.csv') as f:
                    data = f.read()
```

## Security Features

- **Read-Only by Default**: Datasets are mounted read-only to prevent accidental modification
- **Automatic Injection**: No manual configuration in pipelines reduces security risks
- **Isolated Per Task**: Each task gets its own isolated mount

## Verification

### Test Dataset Access

Create a simple test file:

```bash
# On host machine
echo "Dataset test successful!" > /path/to/datasets/test.txt

# Add to LXC container
lxc config device add <container-name> datasets disk \
  source=/path/to/datasets \
  path=/srv/datasets \
  readonly=true
```

Run verification pipeline:

```yaml
jobs:
  - name: verify-dataset
    plan:
      - task: check-mount
        tags: [gpu]
        config:
          platform: linux
          image_resource:
            type: registry-image
            source:
              repository: ubuntu
              tag: latest
          run:
            path: bash
            args:
              - -c
              - |
                echo "=== Dataset Verification ==="
                
                # Check mount exists
                if [ -d "/srv/datasets" ]; then
                  echo "✅ /srv/datasets exists"
                else
                  echo "❌ /srv/datasets not found"
                  exit 1
                fi
                
                # Check read access
                ls -lah /srv/datasets/
                
                # Check test file
                if cat /srv/datasets/test.txt; then
                  echo "✅ Dataset read successful"
                else
                  echo "❌ Cannot read dataset"
                  exit 1
                fi
                
                # Verify read-only
                if touch /srv/datasets/write-test 2>&1 | grep -q "Read-only"; then
                  echo "✅ Confirmed read-only mount"
                else
                  echo "⚠️  Warning: Mount may not be read-only"
                fi
```

## Multiple Dataset Directories

You can mount multiple dataset directories to different paths:

```bash
# Training datasets
lxc config device add <container-name> training-data disk \
  source=/path/to/training-data \
  path=/srv/datasets/training \
  readonly=true

# Validation datasets
lxc config device add <container-name> validation-data disk \
  source=/path/to/validation-data \
  path=/srv/datasets/validation \
  readonly=true

# Model checkpoints
lxc config device add <container-name> checkpoints disk \
  source=/path/to/checkpoints \
  path=/srv/models \
  readonly=false  # Allow write for saving models
```

## Troubleshooting

### Dataset Not Visible in Tasks

1. **Check LXC mount:**
   ```bash
   lxc config device show <container-name>
   lxc exec <container-name> -- ls -la /srv/datasets/
   ```

2. **Verify OCI wrapper is installed:**
   ```bash
   lxc exec <container-name> -- cat /usr/local/bin/runc-gpu-wrapper
   ```
   Should include dataset mount injection logic.

3. **Check worker logs:**
   ```bash
   juju debug-log --include gpu-worker/0 --tail 100
   ```

### Permission Issues

If you see permission errors:

```bash
# Make dataset directory readable
chmod -R a+rX /path/to/datasets/

# Verify ownership
ls -la /path/to/datasets/
```

### Mount Not Updating

If you change the dataset content but tasks see old data:

```bash
# Restart the worker to refresh mounts
juju run gpu-worker/0 restart

# Or restart the container
lxc restart <container-name>
```

## Best Practices

1. **Use Read-Only Mounts**: Always mount datasets read-only unless you need write access
2. **Organize by Purpose**: Use subdirectories for different dataset types
3. **Document Dataset Structure**: Include a README in the dataset directory
4. **Version Your Datasets**: Use dated directories or symlinks for dataset versions
5. **Test First**: Always verify dataset access with a simple test pipeline

## Performance Considerations

- **Local Storage**: For best I/O performance, store datasets on local NVMe/SSD
- **Network Storage**: NFS/SMB mounts work but may impact training performance
- **Dataset Size**: Consider using dataset caching strategies for large datasets
- **Parallel Access**: Multiple tasks can read from the same dataset simultaneously

## Example: Complete ML Training Setup

```bash
# 1. Organize datasets on host
mkdir -p /data/ml-datasets/{training,validation,test}
cp your-data.csv /data/ml-datasets/training/

# 2. Deploy GPU worker
juju deploy concourse-ci-machine gpu-worker \
  --config deployment-mode=worker \
  --config enable-gpu=true

# 3. Wait for deployment
juju status --watch 1s

# 4. Mount datasets
CONTAINER=$(lxc list | grep gpu-worker | awk '{print $2}')
lxc config device add $CONTAINER datasets disk \
  source=/data/ml-datasets \
  path=/srv/datasets \
  readonly=true

# 5. Mount model output directory (writable)
lxc config device add $CONTAINER models disk \
  source=/data/ml-models \
  path=/srv/models \
  readonly=false

# 6. Verify setup
fly -t local execute -c verify-datasets.yaml --tag gpu
```

## Integration with CI/CD

The automatic dataset mounting integrates seamlessly with your CI/CD workflows:

```yaml
jobs:
  - name: model-training-pipeline
    plan:
      # 1. Get code from repository
      - get: ml-code
        trigger: true
      
      # 2. Train model with GPU and datasets
      - task: train
        tags: [gpu]
        config:
          platform: linux
          image_resource:
            type: registry-image
            source:
              repository: pytorch/pytorch
              tag: latest
          inputs:
            - name: ml-code
          run:
            path: bash
            args:
              - -c
              - |
                # Code is in ml-code/
                # Datasets automatically available in /srv/datasets/
                cd ml-code
                python train.py \
                  --data /srv/datasets/training \
                  --output /tmp/model.pth
      
      # 3. Validate model
      - task: validate
        tags: [gpu]
        config:
          platform: linux
          image_resource:
            type: registry-image
            source:
              repository: pytorch/pytorch
              tag: latest
          run:
            path: python
            args:
              - -c
              - |
                # Validation data automatically available
                import torch
                validate_model('/tmp/model.pth', '/srv/datasets/validation')
```

## Advanced: Dynamic Dataset Selection

Use environment variables to select different datasets per pipeline run:

```yaml
jobs:
  - name: train-with-dataset
    plan:
      - task: training
        tags: [gpu]
        params:
          DATASET_NAME: "imagenet-subset-v2"
        config:
          platform: linux
          image_resource:
            type: registry-image
            source:
              repository: pytorch/pytorch
              tag: latest
          run:
            path: bash
            args:
              - -c
              - |
                DATASET_PATH="/srv/datasets/${DATASET_NAME}"
                
                if [ ! -d "$DATASET_PATH" ]; then
                  echo "Error: Dataset $DATASET_NAME not found"
                  exit 1
                fi
                
                echo "Training with dataset: $DATASET_NAME"
                python train.py --data "$DATASET_PATH"
```

## Summary

The dataset mounting system provides:

✅ **Automatic Injection**: No pipeline modifications required
✅ **Secure by Default**: Read-only mounts prevent accidental data corruption
✅ **Flexible Configuration**: Mount multiple datasets to different paths
✅ **High Performance**: Direct access to host storage
✅ **Simple Setup**: Just configure LXC device and it works

For questions or issues, check the troubleshooting section or open an issue on GitHub.
