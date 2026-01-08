#!/bin/bash
# Example deployment script for Concourse CI with GPU support

set -e

echo "=== Concourse CI with GPU Support Deployment ==="
echo ""

# Build charm
echo "Building charm..."
charmcraft pack

CHARM_FILE="./concourse-ci-machine_ubuntu-22.04-amd64.charm"

if [ ! -f "$CHARM_FILE" ]; then
    echo "Error: Charm file not found: $CHARM_FILE"
    exit 1
fi

# Deploy PostgreSQL
echo "Deploying PostgreSQL..."
juju deploy postgresql --channel 14/stable

# Deploy web server
echo "Deploying Concourse web server..."
juju deploy "$CHARM_FILE" web \
  --config mode=web

# Deploy GPU worker
echo "Deploying GPU-enabled worker..."
juju deploy "$CHARM_FILE" worker \
  --config mode=worker \
  --config enable-gpu=true \
  --config gpu-device-ids=all

# Create relations
echo "Creating relations..."
juju relate web:postgresql postgresql:db
juju relate web:web-tsa worker:worker-tsa

echo ""
echo "=== Deployment initiated ==="
echo ""
echo "Monitor progress with: juju status --watch 1s"
echo ""
echo "Once ready, get admin password with:"
echo "  juju run web/leader get-admin-password"
echo ""
echo "Access web UI at: http://<web-ip>:8080"
echo ""
