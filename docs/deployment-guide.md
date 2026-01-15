# Concourse CI Deployment Guide

Quick reference guide for deploying Concourse CI with this charm.

## Prerequisites

- Juju 3.x controller
- Ubuntu 24.04 LTS environment
- Access to Charmhub or local charm file

## Basic Deployment (Single Unit - Web Only)

**Note**: In `mode=auto`, the leader unit acts as the Web node. You need at least **2 units** to have functional Workers. A single-unit deployment provides the Web UI but cannot execute pipelines.

```bash
# Create model
juju add-model concourse

# Deploy PostgreSQL 16
juju deploy postgresql --channel 16/stable --base ubuntu@24.04

# Deploy Concourse CI
juju deploy concourse-ci-machine concourse-ci --config mode=auto --base ubuntu@24.04

# Integrate with database (uses Juju secrets)
juju integrate concourse-ci:postgresql postgresql:database

# Expose web interface
juju expose concourse-ci

# Monitor deployment
juju status --watch 1s
```

## Common Configuration

```bash
# Use port 80 (privileged port supported)
juju config concourse-ci web-port=80

# Set external URL for proper redirects
juju config concourse-ci external-url=http://your-domain.com

# Enable debug logging
juju config concourse-ci log-level=debug
```

## Getting Admin Credentials

```bash
juju run concourse-ci/leader get-admin-password
```

## Port Forwarding (Optional)

If your Concourse unit is in an LXD container and you want to access it via the host's IP:

```bash
# On the host machine
sudo iptables -t nat -A PREROUTING -d <HOST_IP> -p tcp --dport 80 -j DNAT --to-destination <CONTAINER_IP>:80
sudo iptables -t nat -A POSTROUTING -d <CONTAINER_IP> -p tcp --dport 80 -j MASQUERADE
sudo iptables -I DOCKER-USER -d <CONTAINER_IP> -p tcp --dport 80 -j ACCEPT
sudo iptables -I DOCKER-USER -s <CONTAINER_IP> -p tcp --sport 80 -j ACCEPT

# Make persistent
sudo apt install iptables-persistent
sudo netfilter-persistent save
```

## Multi-Unit Deployment

```bash
# Deploy 3 units (1 web + 2 workers automatically)
juju deploy concourse-ci-machine concourse-ci -n 3 --config mode=auto --base ubuntu@24.04
juju deploy postgresql --channel 16/stable --base ubuntu@24.04
juju integrate concourse-ci:postgresql postgresql:database
juju expose concourse-ci
```

## Separate Web and Worker Applications

```bash
# Deploy web
juju deploy concourse-ci-machine web --config mode=web --base ubuntu@24.04
juju deploy postgresql --channel 16/stable --base ubuntu@24.04
juju integrate web:postgresql postgresql:database

# Deploy workers
juju deploy concourse-ci-machine worker -n 2 --config mode=worker --base ubuntu@24.04

# Connect workers to web
juju integrate web:web-tsa worker:worker-tsa

# Expose web
juju expose web
```

## Troubleshooting

### Check service status
```bash
juju status
juju ssh concourse-ci/0 'sudo systemctl status concourse-server.service'
```

### View logs
```bash
juju debug-log --include concourse-ci/0
juju ssh concourse-ci/0 'sudo journalctl -u concourse-server.service -f'
```

### Verify database connection
```bash
juju ssh concourse-ci/0 'sudo cat /var/lib/concourse/config.env | grep POSTGRES'
```

### Check opened ports
```bash
juju ssh concourse-ci/0 'sudo ss -tlnp | grep concourse'
```

## Upgrading

```bash
# Refresh to new charm revision
juju refresh concourse-ci --path=./concourse-ci-machine_amd64.charm

# Or from Charmhub
juju refresh concourse-ci
```

### Storage Constraint Issues

If you encounter an error like `validating storage constraints: charm ... minimum storage size is 10 GB, 1.0 GB specified`, it means your existing deployment has a recorded storage size smaller than what the new charm requires (10GB).

To resolve this, explicitly specify the storage configuration during refresh:

```bash
# Option 1: Use the rootfs pool (works well for LXD/localhost)
juju refresh concourse-ci --channel latest/edge --storage concourse-data=rootfs

# Option 2: Explicitly update the size constraint
juju refresh concourse-ci --channel latest/edge --storage concourse-data=10G
```

This tells Juju to update the application's storage constraints to match the new charm's requirements.

## Removing Applications

```bash
# Remove application and destroy storage
juju remove-application concourse-ci --destroy-storage --no-prompt

# Remove without destroying storage (storage will be detached and preserved)
juju remove-application concourse-ci

# Clean up orphaned storage later if needed
juju storage  # List all storage
juju remove-storage concourse-data/14 --force  # Remove specific detached storage
```

**Note:** By default, `juju remove-application` detaches storage but preserves it. Use `--destroy-storage` to permanently delete storage volumes when removing an application.

## Useful Actions

```bash
# Get admin password
juju run concourse-ci/leader get-admin-password

# Check status
juju run concourse-ci/0 check-status

# Restart services
juju run concourse-ci/0 restart-services

# Upgrade Concourse version
juju config concourse-ci version=7.14.3
```
