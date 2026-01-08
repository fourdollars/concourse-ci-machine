# Concourse CI Deployment Guide

Quick reference guide for deploying Concourse CI with this charm.

## Prerequisites

- Juju 3.x controller
- Ubuntu 24.04 LTS environment
- Access to Charmhub or local charm file

## Basic Deployment (Single Unit)

```bash
# Create model
juju add-model concourse

# Deploy PostgreSQL 16
juju deploy postgresql --channel 16/stable --base ubuntu@24.04

# Deploy Concourse CI
juju deploy concourse-ci-machine concourse-ci --config mode=all --base ubuntu@24.04

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

## Useful Actions

```bash
# Get admin password
juju run concourse-ci/leader get-admin-password

# Check status
juju run concourse-ci/0 check-status

# Restart services
juju run concourse-ci/0 restart-services

# Upgrade Concourse version
juju run concourse-ci/0 upgrade version=7.14.3
```
