**Work in progress**

# Concourse CI Machine Charm

[![GitHub](https://img.shields.io/badge/GitHub-fourdollars/concourse--ci--machine-blue.svg)](https://github.com/fourdollars/concourse-ci-machine)
[![CI](https://github.com/fourdollars/concourse-ci-machine/actions/workflows/ci.yml/badge.svg)](https://github.com/fourdollars/concourse-ci-machine/actions/workflows/ci.yml)
[![Charmhub](https://charmhub.io/concourse-ci-machine/badge.svg)](https://charmhub.io/concourse-ci-machine)

A Juju **machine charm** for deploying [Concourse CI](https://concourse-ci.org/) - a modern, scalable continuous integration and delivery system. This charm supports flexible deployment patterns including single-unit, multi-unit with automatic role assignment, and separate web/worker configurations.

> **Note:** This is a machine charm designed for bare metal, VMs, and LXD deployments. For Kubernetes deployments, see https://charmhub.io/concourse-web and https://charmhub.io/concourse-worker.

## Features

- **Flexible Deployment Modes**: Deploy as auto-scaled web/workers or explicit roles
- **Automatic Role Detection**: Leader unit becomes web server, followers become workers
- **Fully Automated Key Distribution**: TSA keys automatically shared via peer relations - zero manual setup!
- **Secure Random Passwords**: Auto-generated admin password stored in Juju peer data
- **Latest Version Detection**: Automatically downloads the latest Concourse release from GitHub
- **PostgreSQL 16+ Integration**: Full support with Juju secrets API for secure credential management
- **Dynamic Port Configuration**: Change web port on-the-fly with automatic service restart
- **Privileged Port Support**: Run on port 80 with proper Linux capabilities (CAP_NET_BIND_SERVICE)
- **Auto External-URL**: Automatically detects unit IP for external-url configuration
- **Ubuntu 24.04 LTS**: Optimized for Ubuntu 24.04 LTS
- **Container Runtime**: Uses containerd with LXD-compatible configuration
- **Automatic Key Management**: TSA keys, session signing keys, and worker keys auto-generated
- **Prometheus Metrics**: Optional metrics endpoint for monitoring
- **Download Progress**: Real-time installation progress in Juju status
- **GPU Support**: NVIDIA GPU workers for ML/AI workloads ([GPU Guide](docs/gpu-support.md))
- **Dataset Mounting**: Automatic dataset injection for GPU tasks ([Dataset Guide](docs/dataset-mounting.md))
- **🆕 General Folder Mounting**: Automatic discovery and mounting of ANY folder under `/srv` ([General Mounting Guide](docs/general-mounting.md))
  - ✅ Zero configuration - just mount folders to `/srv` and go
  - ✅ Read-only by default for data safety
  - ✅ Writable folders with `_writable` or `_rw` suffix
  - ✅ Multiple concurrent folders (datasets, models, outputs, caches)
  - ✅ Works on both GPU and non-GPU workers
  - ✅ Automatic permission validation and fail-fast
  - ✅ Backward compatible with existing GPU dataset mounting

## Quick Start

### Prerequisites

- Juju 3.x
- Ubuntu 24.04 LTS (on Juju-managed machines)
- PostgreSQL charm 16/stable (for web server)

### Basic Deployment (Auto Mode)

```bash
# Create a Juju model
juju add-model concourse

# Deploy PostgreSQL
juju deploy postgresql --channel 16/stable --base ubuntu@24.04

# Deploy Concourse CI charm as application "concourse-ci"
juju deploy concourse-ci-machine concourse-ci --config mode=auto

# Relate to database (uses PostgreSQL 16 client interface with Juju secrets)
juju integrate concourse-ci:postgresql postgresql:database

# Expose the web interface (opens port in Juju)
juju expose concourse-ci

# Wait for deployment (takes ~5-10 minutes)
juju status --watch 1s
```

The charm automatically:
- Reads database credentials from Juju secrets
- Configures the external URL based on unit IP
- Opens the configured web port (default: 8080)
- Generates and stores admin password in peer relation data

**Naming Convention:**
- **Charm name**: `concourse-ci-machine` (what you deploy from Charmhub)
- **Application name**: `concourse-ci` (used throughout this guide)
- **Unit names**: `concourse-ci/0`, `concourse-ci/1`, etc.

Once deployed, get credentials with `juju run concourse-ci/leader get-admin-password`

### Multi-Unit Deployment with Auto Mode (Recommended)

Deploy multiple units with automatic role assignment and key distribution:

```bash
# Deploy PostgreSQL
juju deploy postgresql --channel 16/stable --base ubuntu@24.04

# Deploy Concourse charm (named "concourse-ci") with 1 web + 2 workers
juju deploy concourse-ci-machine concourse-ci -n 3 --config mode=auto

# Relate to database (using application name "concourse-ci")
juju relate concourse-ci:postgresql postgresql:database

# Check deployment
juju status
```

**Result:**
- `concourse-ci/0` (leader): Web server
- `concourse-ci/1-2`: Workers
- **All keys automatically distributed via peer relations!** ✨

**Note:** Application is named `concourse-ci` for easier reference (shorter than `concourse-ci-machine`)

### Separated Web/Worker Deployment (For Independent Scaling)

For maximum flexibility with separate applications:

```bash
# Deploy PostgreSQL
juju deploy postgresql --channel 16/stable --base ubuntu@24.04

# Deploy web server (1 unit)
juju deploy concourse-ci-machine web --config mode=web

# Deploy workers (2 units)  
juju deploy concourse-ci-machine worker -n 2 --config mode=worker

# Relate web to database
juju relate web:postgresql postgresql:database

# Relate web and worker for automatic TSA key exchange
juju relate web:web-tsa worker:worker-tsa

# Check deployment
juju status
```

**Result:**
- `web/0`: Web server only
- `worker/0`, `worker/1`: Workers only connected via TSA

**Note**: The `web-tsa` / `worker-tsa` relation automatically handles SSH key exchange between web and worker applications, eliminating the need for manual key management.

## Deployment Modes

The charm supports three deployment modes via the `mode` configuration:

### 1. `auto` (Multi-Unit - Fully Automated ✨)
Leader unit runs web server, non-leader units run workers. **Keys automatically distributed via peer relations!**

**Note**: You need at least **2 units** for this mode to have functional workers (Unit 0 = Web, Unit 1+ = Workers).

```bash
juju deploy concourse-ci-machine concourse-ci -n 3 --config mode=auto
juju relate concourse-ci:postgresql postgresql:database
```

**Best for:** Production, scalable deployments
**Key Distribution:** ✅ **Fully Automatic** - zero manual intervention required!

### 2. `web` + `worker` (Separate Apps - Automatic TSA Setup)
Deploy web and workers as separate applications for independent scaling.

```bash
# Web application
juju deploy concourse-ci-machine web --config mode=web

# Worker application (scalable)
juju deploy concourse-ci-machine worker -n 2 --config mode=worker

# Relate web to PostgreSQL
juju relate web:postgresql postgresql:database

# Relate web and worker for automatic TSA key exchange
juju relate web:web-tsa worker:worker-tsa
```

**Best for:** Independent scaling of web and workers
**Key Distribution:** ✅ Automatic via `web-tsa` / `worker-tsa` relation

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mode` | string | `auto` | Deployment mode: auto, web, or worker |
| `version` | string | `latest` | Concourse version to install (auto-detects latest from GitHub) |
| `web-port` | int | `8080` | Web UI and API port |
| `worker-procs` | int | `1` | Number of worker processes per unit |
| `log-level` | string | `info` | Log level: debug, info, warn, error |
| `enable-metrics` | bool | `true` | Enable Prometheus metrics on port 9391 |
| `external-url` | string | (auto) | External URL for webhooks and OAuth |
| `initial-admin-username` | string | `admin` | Initial admin username |
| `container-placement-strategy` | string | `volume-locality` | Container placement: volume-locality, random, etc. |
| `max-concurrent-downloads` | int | `10` | Max concurrent resource downloads |
| `containerd-dns-proxy-enable` | bool | `false` | Enable containerd DNS proxy |
| `containerd-dns-server` | string | `1.1.1.1,8.8.8.8` | DNS servers for containerd containers |

### Changing Configuration

Configuration changes are applied dynamically with automatic service restart.

```bash
# Set custom web port (automatically restarts service)
juju config concourse-ci web-port=9090

# Change to privileged port 80 (requires CAP_NET_BIND_SERVICE - already configured)
juju config concourse-ci web-port=80

# Enable debug logging
juju config concourse-ci log-level=debug

# Set external URL (auto-detects unit IP if not set)
juju config concourse-ci external-url=https://ci.example.com
```

### Upgrading Concourse Version

Use the `upgrade` action to change Concourse CI version (update the `version` configuration first to ensure the change persists across charm refreshes):

```bash
# Set version configuration first (essential for persistence)
juju config concourse-ci version=7.14.3

# Trigger the upgrade action (automatically upgrades all workers)
juju config concourse-ci version=7.14.3

# Downgrade is also supported (update config then run action)
juju config concourse-ci version=7.12.1
juju config concourse-ci version=7.12.1
```

**Auto-upgrade behavior:**
- When the web server (leader in mode=auto) is upgraded, all workers automatically upgrade to match
- Works across separate applications connected via TSA relations
- Workers show "Auto-upgrading Concourse CI to X.X.X..." during automatic upgrades

**Note**: The `web-port` configuration supports dynamic changes including privileged ports (< 1024) thanks to `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the systemd service.

## Using Concourse

### Access the Web UI

1. Get the web server IP:
```bash
juju status
```

2. Check the exposed port (shown in Ports column):
```bash
juju status concourse-ci
# Look for: Ports column showing "80/tcp" or "8080/tcp"
```

3. Open in browser: `http://<web-unit-ip>:<port>`

4. Get the admin credentials:
```bash
juju run concourse-ci/leader get-admin-password
```

Example output:
```
message: Use these credentials to login to Concourse web UI
password: 01JfF@I!9W^0%re!3I!hyy3C
username: admin
```

**Security**: A random password is automatically generated on first deployment and stored securely in Juju peer relation data. All units in the deployment share the same credentials.

### Using Fly CLI

The Fly CLI is Concourse's command-line tool for managing pipelines:

```bash
# Download fly from your Concourse instance
curl -Lo fly "http://<web-unit-ip>:8080/api/v1/cli?arch=amd64&platform=linux"
chmod +x fly
sudo mv fly /usr/local/bin/

# Get credentials
ADMIN_PASSWORD=$(juju run concourse-ci/leader get-admin-password --format=json | jq -r '."unit-concourse-ci-2".results.password')

# Login
fly -t prod login -c http://<web-unit-ip>:8080 -u admin -p "$ADMIN_PASSWORD"

# Sync fly version
fly -t prod sync
```

### Create Your First Pipeline

**⚠️ Important**: This charm uses containerd runtime. All tasks **must** include an `image_resource`.

1. Create a pipeline file `hello.yml`:
```yaml
jobs:
- name: hello-world
  plan:
  - task: say-hello
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: busybox
      run:
        path: sh
        args:
        - -c
        - |
          echo "=============================="
          echo "Hello from Concourse CI!"
          echo "Date: $(date)"
          echo "=============================="
```

2. Set the pipeline:
```bash
fly -t prod set-pipeline -p hello -c hello.yml
fly -t prod unpause-pipeline -p hello
```

3. Trigger the job:
```bash
fly -t prod trigger-job -j hello/hello-world -w
```

**Note**: Common lightweight images: `busybox` (~2MB), `alpine` (~5MB), `ubuntu` (~28MB)

## Scaling

### Add More Workers

```bash
# Add 2 more worker units to the concourse-ci application
juju add-unit concourse-ci -n 2

# Verify workers
juju ssh concourse-ci/0  # SSH to unit 0 of concourse-ci application
fly -t local workers
```

### Remove Workers

```bash
# Remove specific unit
juju remove-unit concourse-ci/3
```

## Relations

### Required Relations

#### PostgreSQL (Required for Web Server)
The web server requires a PostgreSQL database:

```bash
juju relate concourse-ci:postgresql postgresql:database
```

**Supported PostgreSQL Charms:**
- `postgresql` (16/stable recommended)
- Any charm providing the `postgresql` interface

### Optional Relations

#### Monitoring
Concourse exposes Prometheus metrics on port 9391:

```bash
juju relate concourse-ci:monitoring prometheus:target
```

#### Peer Relation
Units automatically coordinate via the `concourse-peer` relation (automatic, no action needed).

## Storage

The charm uses Juju storage for persistent data:

```bash
# Deploy with specific storage
juju deploy concourse-ci-machine concourse-ci --storage concourse-data=20G

# Add storage to existing unit
juju add-storage concourse-ci/0 concourse-data=10G
```

Storage is mounted at `/var/lib/concourse`.

## GPU Support

Concourse workers can utilize NVIDIA GPUs for ML/AI workloads, GPU-accelerated builds, and compute-intensive tasks.

### Prerequisites

- NVIDIA GPU hardware on the host machine
- NVIDIA drivers installed on the host (tested with driver 580.95+)
- **For LXD/containers:** GPU passthrough configured (see below)

**Note:** The charm automatically installs `nvidia-container-toolkit` and configures the GPU runtime. No manual setup required!

### Quick Start: Deploy with GPU

Complete deployment from scratch:

```bash
# 1. Deploy PostgreSQL
juju deploy postgresql --channel 16/stable --base ubuntu@24.04

# 2. Deploy web server
juju deploy concourse-ci-machine web --config mode=web

# 3. Deploy GPU-enabled worker
juju deploy concourse-ci-machine worker \
  --config mode=worker \
  --config enable-gpu=true

# 4. Add GPU to LXD container (only manual step for localhost cloud)
lxc config device add <container-name> gpu0 gpu
# Example: lxc config device add juju-abc123-0 gpu0 gpu

# 5. Create relations
juju relate web:postgresql postgresql:database
juju relate web:web-tsa worker:worker-tsa

# 6. Check status
juju status worker
# Expected: "Worker ready (GPU: 1x NVIDIA)"
```

### Enable GPU on Existing Worker

```bash
# Enable GPU on already deployed worker
juju config worker enable-gpu=true
```

### LXD GPU Passthrough (One-time setup)

If deploying on LXD (localhost cloud), add GPU to the container:

```bash
# Find your worker container name
lxc list | grep juju

# Add GPU device (requires container restart)
lxc config device add <container-name> gpu0 gpu

# Example:
lxc config device add juju-abc123-0 gpu0 gpu
```

**Everything else is automated!** The charm will:
- ✅ Install nvidia-container-toolkit
- ✅ Create GPU wrapper script
- ✅ Configure runtime for GPU passthrough
- ✅ Set up automatic GPU device injection

### GPU Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `enable-gpu` | `false` | Enable GPU support for this worker |
| `gpu-device-ids` | `all` | GPU devices to expose: "all" or "0,1,2" |

### GPU Worker Tags

When GPU is enabled, workers are automatically tagged:
- `gpu` - Worker has GPU
- `gpu-type=nvidia` - GPU vendor type
- `gpu-count=N` - Number of GPUs available
- `gpu-devices=0,1` - Specific device IDs (if configured)

### Example: GPU Pipeline

Create a pipeline that targets GPU-enabled workers:

```yaml
jobs:
- name: train-model
  plan:
  - task: gpu-training
    tags: [gpu]  # Target GPU-enabled workers
    config:
      platform: linux
      image_resource:
        type: registry-image
        source:
          repository: nvidia/cuda
          tag: 13.1.0-runtime-ubuntu24.04
      run:
        path: sh
        args:
        - -c
        - |
          # Verify GPU access
          nvidia-smi
          
          # Run your GPU workload
          python train.py --use-gpu

- name: gpu-benchmark
  plan:
  - task: benchmark
    tags: [gpu, gpu-type=nvidia, gpu-count=1]  # More specific targeting
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

### Verifying GPU Access

```bash
# Check worker status
juju status worker
# Should show: "Worker ready (GPU: 1x NVIDIA)"

# Verify GPU tags in Concourse
fly -t local workers
# Worker should show tags: gpu, gpu-type=nvidia, gpu-count=1
```

### Common GPU Images

- `nvidia/cuda:13.1.0-base-ubuntu24.04` - CUDA base (~174MB)
- `nvidia/cuda:13.1.0-runtime-ubuntu24.04` - CUDA runtime (~1.38GB)
- `nvidia/cuda:13.1.0-devel-ubuntu24.04` - CUDA development (~3.39GB)
- `tensorflow/tensorflow:latest-gpu` - TensorFlow with GPU
- `pytorch/pytorch:latest` - PyTorch with GPU

### GPU Troubleshooting

**Worker shows "GPU enabled but no GPU detected"**
- Verify GPU present: `nvidia-smi`
- Check driver installation: `nvidia-smi`

**Container cannot access GPU**
- Verify nvidia-container-runtime: `which nvidia-container-runtime`
- Check containerd config: `cat /etc/containerd/config.toml`
- Restart containerd: `sudo systemctl restart containerd`

**GPU not showing in task**
- Ensure using NVIDIA CUDA base image
- Run `nvidia-smi` in task to debug
- Check worker tags: `fly -t local workers`

## Troubleshooting

### Charm Shows "Blocked" Status

**Cause:** Usually means PostgreSQL relation is missing (for web units).

**Fix:**
```bash
juju relate concourse-ci:postgresql postgresql:database
```

### Web Server Won't Start

**Check logs:**
```bash
juju debug-log --include concourse-ci/0 --replay --no-tail | tail -50

# Or SSH and check systemd
juju ssh concourse-ci/0
sudo journalctl -u concourse-server -f
```

**Common issues:**
- Database not configured: Check PostgreSQL relation
- Auth configuration missing: Check `/var/lib/concourse/config.env`
- Port already in use: Change `web-port` config

### Workers Not Connecting

**Check worker status:**
```bash
juju ssh concourse-ci/1  # Worker unit
sudo systemctl status concourse-worker
sudo journalctl -u concourse-worker -f
```

**Common issues:**
- TSA keys not generated: Check `/var/lib/concourse/keys/`
- Containerd not running: `sudo systemctl status containerd`
- Network connectivity: Ensure workers can reach web server


### View Configuration

```bash
juju ssh concourse-ci/0
sudo cat /var/lib/concourse/config.env
```

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                     Web Server                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐         │
│  │ Web UI/API │  │    TSA     │  │ Scheduler  │         │
│  └────────────┘  └────────────┘  └────────────┘         │
│         │              │                 │              │
│         └──────────────┴─────────────────┘              │
│                        │                                │
└────────────────────────┼────────────────────────────────┘
                         │
                         │ (SSH over TSA)
                         │
        ┌────────────────┴────────────────┐
        │                                 │
  ┌─────▼──────┐                   ┌─────▼──────┐
  │  Worker 1  │                   │  Worker 2  │
  │┌──────────┐│                   │┌──────────┐│
  ││Container ││                   ││Container ││
  ││Runtime   ││                   ││Runtime   ││
  │└──────────┘│                   │└──────────┘│
  └────────────┘                   └────────────┘
```

... see https://concourse-ci.org/internals.html

### Key Directories

- `/opt/concourse/`: Concourse binaries
- `/var/lib/concourse/`: Data and configuration
- `/var/lib/concourse/keys/`: TSA and worker keys
- `/var/lib/concourse/worker/`: Worker runtime directory

### Systemd Services

- `concourse-server.service`: Web server (runs as `concourse` user)
- `concourse-worker.service`: Worker (runs as `root`)

## Development

### Building from Source

```bash
# Install charmcraft
sudo snap install charmcraft --classic

# Clone repository
git clone https://github.com/fourdollars/concourse-ci-machine.git
cd concourse-ci-machine

# Build charm
charmcraft pack

# Deploy locally
juju deploy ./concourse-ci-machine_amd64.charm
```

### Project Structure

```
concourse-ci-machine/
├── src/
│   └── charm.py                  # Main charm logic
├── lib/
│   ├── concourse_common.py       # Shared utilities
│   ├── concourse_installer.py    # Installation logic
│   ├── concourse_web.py          # Web server management
│   └── concourse_worker.py       # Worker management
├── metadata.yaml                 # Charm metadata
├── config.yaml                   # Configuration options
├── charmcraft.yaml               # Build configuration
├── actions.yaml                  # Charm actions
└── README.md                     # This file
```

## Security

### Initial Setup

1. **Change default password immediately:**
```bash
fly -t prod login -c http://<ip>:8080 -u admin -p admin
# Use web UI to change password in team settings
```

2. **Configure proper authentication:**
   - Set up OAuth providers (GitHub, GitLab, etc.)
   - Use Juju secrets for credentials
   - Enable HTTPS with reverse proxy (nginx/haproxy)

3. **Network security:**
   - Use Juju spaces to isolate networks
   - Configure firewall rules to restrict access
   - Use private PostgreSQL endpoints

### Database Credentials

Database credentials are passed securely via Juju relations, not environment variables.

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

This charm is licensed under the Apache 2.0 License. See [LICENSE](LICENSE) for details.

## Resources

- **Concourse CI**: https://concourse-ci.org/
- **Documentation**: https://concourse-ci.org/docs.html
- **Charm Hub**: https://charmhub.io/concourse-ci
- **Source Code**: https://github.com/fourdollars/concourse-ci-machine
- **Issue Tracker**: https://github.com/fourdollars/concourse-ci-machine/issues
- **Juju**: https://juju.is/

## Support

- **Community Support**: Open an issue on GitHub
- **Commercial Support**: Contact maintainers

## Acknowledgments

- Concourse CI team for the amazing CI/CD system
- Canonical for Juju and the Operator Framework
- Contributors to this charm
