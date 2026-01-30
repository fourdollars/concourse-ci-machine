# GPU Support Implementation Guide

## Overview

This implementation adds NVIDIA GPU support to Concourse CI workers, enabling ML/AI workloads, GPU-accelerated builds, and compute-intensive tasks.

## Quick Links

- 📊 **[Dataset Mounting Guide](dataset-mounting.md)** - Mount datasets for ML training workflows
- 🚀 **[Deployment Guide](deployment-guide.md)** - General deployment instructions
- 📘 **[Main README](README.md)** - Charm overview and features

## What's Been Added

### 1. Configuration Options (config.yaml)

- `enable-gpu` (boolean, default: false) - Enable GPU support for worker
- `gpu-device-ids` (string, default: "all") - Specify which GPUs to expose

### 2. GPU Detection (lib/concourse_common.py)

- `detect_nvidia_gpus()` - Detect NVIDIA GPUs using nvidia-smi
- `verify_nvidia_container_runtime()` - Verify nvidia-container-runtime is available

### 3. Worker GPU Support (lib/concourse_worker.py)

- `configure_containerd_for_gpu()` - Configure containerd with NVIDIA runtime
- `_get_gpu_tags()` - Generate worker tags based on GPU configuration
- `get_gpu_status_message()` - Get GPU status for unit status display

### 4. Charm Integration (src/charm.py)

- GPU configuration during install (if enabled)
- GPU reconfiguration on config-changed events
- GPU status in unit status messages

### 5. Documentation (README.md)

- Prerequisites section
- Configuration examples
- GPU pipeline examples
- Troubleshooting guide

## Prerequisites (Your Environment)

✅ NVIDIA GPU: RTX A500 Laptop GPU
✅ NVIDIA Driver: 580.95.05
✅ nvidia-container-runtime: /usr/bin/nvidia-container-runtime
✅ Juju model on local machine

**IMPORTANT: LXD GPU Passthrough Required**

When using Juju with LXD (localhost cloud), GPU devices must be passed through to containers. See [LXD GPU Setup](#lxd-gpu-setup) below.

## Quick Start

### Deploy Worker with GPU

```bash
# Deploy PostgreSQL
juju deploy postgresql --channel 14/stable

# Deploy web server
juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm web \
  --config mode=web

# Deploy GPU-enabled worker
juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm worker \
  --config mode=worker \
  --config enable-gpu=true

# Create relations
juju relate web:postgresql postgresql:db
juju relate web:tsa worker:flight

# Wait for deployment
juju status --watch 1s
```

### Verify GPU Support

```bash
# Check worker status (should show GPU info)
juju status worker
# Expected: "Worker ready (GPU: 1x NVIDIA)"

# Check workers have GPU tags in Concourse
fly -t <target> workers
# Expected tags: gpu, gpu-type=nvidia, gpu-count=1

# Check automation logs if needed
juju debug-log --include worker/0 | grep -i gpu
```

## Dataset Mounting for ML Workflows

GPU workers automatically support dataset mounting for ML training pipelines. The charm includes an OCI runtime wrapper that injects dataset mounts into task containers.

### Quick Setup

```bash
# 1. Find your GPU worker's LXC container
juju status gpu-worker
# Note the machine number (e.g., "4")

# 2. Identify the container name
CONTAINER=$(lxc list | grep "juju-.*-4" | awk '{print $2}')

# 3. Mount your dataset directory
lxc config device add $CONTAINER datasets disk \
  source=/path/to/your/datasets \
  path=/srv/datasets \
  readonly=true

# 4. Verify the mount
lxc exec $CONTAINER -- ls -lah /srv/datasets/
```

### Automatic Availability in Tasks

Once mounted via LXC, datasets are **automatically available** in all GPU tasks at `/srv/datasets`:

```yaml
jobs:
  - name: train-model
    plan:
      - task: training
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
                # /srv/datasets automatically available!
                import os
                print(f"Datasets: {os.listdir('/srv/datasets')}")
```

**No pipeline modifications required!** The OCI wrapper automatically injects the mount.

For detailed instructions, examples, and troubleshooting, see **[dataset-mounting.md](dataset-mounting.md)**.

### Test GPU Pipeline

Create `gpu-test.yaml`:

```yaml
jobs:
- name: gpu-check
  plan:
  - task: nvidia-smi
    tags: [gpu]
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: nvidia/cuda
          tag: 13.1.0-base-ubuntu24.04
      run:
        path: nvidia-smi
```

Deploy and run:

```bash
# Get web IP
WEB_IP=$(juju status web/0 --format=json | jq -r '.applications.web.units["web/0"]["public-address"]')

# Get admin password
ADMIN_PASS=$(juju run web/leader get-admin-password --format=json | jq -r '."unit-web-0".results.password')

# Login to Concourse
fly -t gpu login -c http://$WEB_IP:8080 -u admin -p "$ADMIN_PASS"

# Set pipeline
fly -t gpu set-pipeline -p gpu-test -c gpu-test.yaml
fly -t gpu unpause-pipeline -p gpu-test

# Trigger job
fly -t gpu trigger-job -j gpu-test/gpu-check -w
```

Expected output: nvidia-smi output showing your RTX A500 GPU

## Implementation Details

### How It Works

1. **GPU Detection**
   - Uses `nvidia-smi` to detect GPU count, models, and driver version
   - Validates nvidia-container-runtime availability

2. **Containerd Configuration**
   - Adds NVIDIA runtime configuration to `/etc/containerd/config.toml`
   - Sets nvidia as default runtime when GPU enabled
   - Restarts containerd service

3. **Worker Tagging**
   - Automatically tags workers with GPU capabilities
   - Tags: `gpu`, `gpu-type=nvidia`, `gpu-count=N`
   - Allows pipeline tasks to target GPU workers

4. **Configuration Flow**
   ```
   enable-gpu=true
   ↓
   Detect GPUs (nvidia-smi)
   ↓
   Verify nvidia-container-runtime
   ↓
   Configure containerd with nvidia runtime
   ↓
   Add GPU tags to worker config
   ↓
   Start/restart worker with GPU support
   ```

### Files Modified

- `config.yaml` - Added GPU configuration options
- `lib/concourse_common.py` - Added GPU detection functions
- `lib/concourse_worker.py` - Added GPU configuration and tagging
- `src/charm.py` - Integrated GPU setup in install/config events
- `README.md` - Added GPU support documentation

### Device Selection

**All GPUs (default):**
```bash
juju config worker gpu-device-ids=all
```
Worker tags: `gpu`, `gpu-type=nvidia`, `gpu-count=1`

**Specific GPUs:**
```bash
juju config worker gpu-device-ids=0,2
```
Worker tags: `gpu`, `gpu-type=nvidia`, `gpu-count=2`, `gpu-devices=0,2`

## Advanced Usage

### Multiple GPU Workers

```bash
# Deploy 3 GPU workers
juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm worker -n 3 \
  --config mode=worker \
  --config enable-gpu=true
```

### Mixed Worker Fleet

```bash
# 2 GPU workers
juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm gpu-worker -n 2 \
  --config mode=worker \
  --config enable-gpu=true

# 4 regular workers
juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm cpu-worker -n 4 \
  --config mode=worker
```

Pipeline can target specific workers:
```yaml
- task: train-model
  tags: [gpu]  # Only on GPU workers
  
- task: build-app
  tags: []  # Any worker
```

### Pipeline Examples

**TensorFlow Training:**
```yaml
- task: train
  tags: [gpu, gpu-count=1]
  config:
    platform: linux
    image_resource:
      type: registry-image
      source:
        repository: tensorflow/tensorflow
        tag: latest-gpu
    run:
      path: python
      args: [train.py]
```

**PyTorch Training:**
```yaml
- task: train
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
      args: ["-c", "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"]
```

**CUDA Compilation:**
```yaml
- task: build
  tags: [gpu]
  config:
    platform: linux
    image_resource:
      type: registry-image
      source:
        repository: nvidia/cuda
        tag: 13.1.0-devel-ubuntu24.04
    run:
      path: sh
      args:
      - -c
      - |
        nvcc --version
        nvcc my_cuda_program.cu -o my_program
```

## Troubleshooting

### GPU Not Detected

```bash
# Check GPU hardware
nvidia-smi

# Check driver
modinfo nvidia

# Check container runtime
which nvidia-container-runtime
nvidia-container-runtime --version
```

### Containerd Configuration Issues

```bash
# Check containerd config
sudo cat /etc/containerd/config.toml | grep -A 5 nvidia

# Restart containerd
sudo systemctl restart containerd
sudo systemctl status containerd

# Check logs
sudo journalctl -u containerd -n 100
```

### Worker Not Starting

```bash
# Check worker logs
sudo journalctl -u concourse-worker -n 100 -f

# Check worker config
sudo cat /var/lib/concourse/config.env

# Restart worker
sudo systemctl restart concourse-worker
```

### Container Cannot Access GPU

```bash
# Test GPU access manually
sudo ctr run --rm --runtime io.containerd.runc.v2 \
  docker.io/nvidia/cuda:13.1.0-base-ubuntu24.04 \
  test-gpu nvidia-smi

# If this fails, check:
# 1. nvidia-container-runtime is installed
# 2. containerd config has nvidia runtime
# 3. containerd has been restarted
```

## Testing Checklist

- [x] GPU detection works (`detect_nvidia_gpus()`)
- [x] Container runtime verified (`verify_nvidia_container_runtime()`)
- [x] Worker tags generated correctly (`_get_gpu_tags()`)
- [x] Charm builds successfully
- [ ] Worker starts with GPU enabled
- [ ] Containerd configured with nvidia runtime
- [ ] GPU tags appear in worker registration
- [ ] Pipeline can target GPU workers
- [ ] GPU accessible in task containers

## Next Steps

1. **Deploy and Test**
   ```bash
   juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm worker \
     --config mode=worker \
     --config enable-gpu=true
   ```

2. **Verify GPU Access**
   - Check worker status shows GPU info
   - Run test pipeline with nvidia-smi
   - Verify GPU visible in task output

3. **Production Use**
   - Deploy multiple GPU workers for scale
   - Use worker tags for job targeting
   - Monitor GPU utilization

## LXD GPU Setup

When deploying on LXD (Juju localhost cloud), GPU devices must be passed through to containers.

### Option 1: Pass GPU to Specific Container (After Deployment)

```bash
# Find the worker container name
lxc list | grep worker

# Add GPU device (container will automatically restart)
lxc config device add <container-name> gpu0 gpu

# Example:
lxc config device add juju-abc123-0 gpu0 gpu

# Verify GPU in container (check status)
juju status worker
# Should show: "Worker ready (GPU: 1x NVIDIA)"

# If status doesn't show GPU, trigger reconfiguration
juju config worker enable-gpu=false
juju config worker enable-gpu=true
```

### Option 2: Create GPU-Enabled Profile (Before Deployment)

```bash
# Create LXD profile with GPU
lxc profile create gpu-profile
lxc profile device add gpu-profile gpu0 gpu

# Use Juju constraints to apply profile (requires manual LXD config)
# This is more complex and requires Juju 3.x features
```

### Option 3: Deploy on Bare Metal/VM (Recommended for GPU)

For production GPU workloads, deploy workers on bare metal or VMs instead of LXD:

```bash
# Add a MAAS machine or manual cloud
juju add-machine ssh:user@gpu-host

# Deploy worker to specific machine
juju deploy ./concourse-ci-machine_ubuntu-22.04-amd64.charm worker \
  --to 1 \
  --config mode=worker \
  --config enable-gpu=true
```

### Verifying GPU Passthrough

```bash
# Check worker status shows GPU
juju status worker
# Expected: "Worker ready (GPU: 1x NVIDIA)"

# Check Concourse workers list
fly -t <target> workers
# Should show GPU tags: gpu, gpu-type=nvidia, gpu-count=1

# Run a test pipeline with nvidia-smi to verify GPU access
fly -t <target> trigger-job -j <pipeline>/<gpu-job> -w
```

## Future Enhancements

- AMD ROCm support (`gpu-driver=amd`)
- Intel GPU support (`gpu-driver=intel`)
- GPU metrics (prometheus)
- Automatic driver installation
- GPU fraction/MIG support
- GPU health checks
- Automatic LXD GPU profile configuration

## Support

For issues or questions:
- Check logs: `juju debug-log --include worker | grep -i gpu`
- GPU status: `juju status worker` (should show GPU count)
- Worker tags: `fly -t <target> workers` (should show GPU tags)
