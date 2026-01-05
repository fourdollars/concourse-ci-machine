# Quick Start: Dataset Mounting

Mount datasets into GPU worker tasks in 2 commands.

## TL;DR

```bash
# 1. Mount your dataset
../scripts/mount-datasets.sh gpu-worker /path/to/your/datasets

# 2. Restart the worker
lxc exec juju-<model>-<machine> -- systemctl restart concourse-worker

# 3. Done! Use /srv/datasets in your pipelines
```

## Example

```bash
# Mount ML training data
../scripts/mount-datasets.sh gpu-worker /data/imagenet-subset

# Output shows restart command:
#   lxc exec juju-e16396-4 -- systemctl restart concourse-worker

# Run the restart command
lxc exec juju-e16396-4 -- systemctl restart concourse-worker

# Verify
fly -t local execute -c - --tag gpu <<EOF
platform: linux
image_resource:
  type: registry-image
  source: {repository: ubuntu, tag: latest}
run:
  path: sh
  args: [-c, 'ls -lah /srv/datasets']
EOF
```

## In Your Pipeline

No modifications needed! Just access `/srv/datasets`:

```yaml
jobs:
  - name: train-model
    plan:
      - task: training
        tags: [gpu]  # Runs on GPU worker
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
                import os
                # Dataset automatically available!
                print(f"Found: {os.listdir('/srv/datasets')}")
```

## Advanced Usage

### Custom Mount Point

```bash
../scripts/mount-datasets.sh gpu-worker /data/imagenet /srv/imagenet
```

### Multiple Applications

```bash
../scripts/mount-datasets.sh gpu-worker-a /data/ml-datasets
../scripts/mount-datasets.sh gpu-worker-b /data/ml-datasets
```

## Troubleshooting

**Dataset not visible?**
1. Check LXC mount: `lxc config device show <container>`
2. Verify in container: `lxc exec <container> -- ls /srv/datasets`
3. Restart worker: `lxc exec <container> -- systemctl restart concourse-worker`

**Permission denied?**
```bash
# Make readable
chmod -R a+rX /path/to/datasets
```

**Wrong container name?**
```bash
# Find container
lxc list | grep juju
```

## Full Documentation

- **Complete Guide**: [DATASET_MOUNTING.md](DATASET_MOUNTING.md)
- **GPU Support**: [GPU_SUPPORT.md](GPU_SUPPORT.md)
- **Main README**: [../README.md](../README.md)

## Summary

✅ No pipeline modifications
✅ Automatic in all GPU tasks  
✅ Read-only and secure
✅ Survives restarts
