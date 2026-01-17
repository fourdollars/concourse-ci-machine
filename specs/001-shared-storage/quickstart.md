# Quickstart Guide: Shared Storage for Concourse CI
**Feature**: 001-shared-storage  
**Target Users**: Juju operators, DevOps engineers  
**Time to Complete**: 15-20 minutes

## Overview
This guide shows you how to deploy Concourse CI with shared storage across multiple units, eliminating redundant binary downloads and reducing disk usage from N×binary size to ~1.15×binary size.

## Prerequisites
- Juju 3.1+ installed and bootstrapped
- Access to a shared filesystem storage pool (NFS, Ceph, etc.)
- Basic understanding of Juju storage concepts

## Architecture
```
┌─────────────────────────────────────────────────────────┐
│ Shared Storage Volume (/var/lib/concourse/)            │
├─────────────────────────────────────────────────────────┤
│ ├── bin/              ← Web/leader writes binaries     │
│ ├── keys/             ← Shared TSA keys                │
│ ├── .installed_version ← Version marker                │
│ └── worker/           ← Per-unit isolated state        │
│     ├── concourse-ci-0/                                 │
│     ├── concourse-ci-1/                                 │
│     └── concourse-ci-2/                                 │
└─────────────────────────────────────────────────────────┘
         ▲                ▲                ▲
         │                │                │
    Web/Leader       Worker 1         Worker 2
  (downloads once) (waits & reuses) (waits & reuses)
```

## Deployment Modes Overview

The charm supports 4 deployment modes:

| Mode | Description | Units Supported | Shared Storage |
|------|-------------|-----------------|----------------|
| `auto` | Leader=web, non-leaders=workers | **Multiple** ✅ | **Recommended** ✅ |
| `web` | Web server only | **Single only** | Future: load balancing |
| `worker` | Worker only | **Multiple** ✅ | Requires separate web |

**Important Constraints**:
- `mode=web`: **Single unit only** (multiple units not yet implemented)
- `mode=worker`: Multiple units supported ✅
- `mode=auto`: Multiple units supported ✅ (**Recommended for shared storage**)

## Step 1: Create Shared Storage Pool

### Option A: NFS Storage (Recommended for Testing)
```bash
# On Juju controller machine, create storage pool
juju create-storage-pool shared-nfs nfs \
  server=10.0.1.100 \
  share=/exports/concourse-storage
```

### Option B: Ceph Storage (Recommended for Production)
```bash
# Create Ceph storage pool (requires Ceph cluster)
juju create-storage-pool shared-ceph ceph-rbd \
  pool-name=juju-concourse
```

### Verify Storage Pool
```bash
juju storage-pools
# Should show your shared-nfs or shared-ceph pool
```

## Deployment Scenario A: mode=auto (Recommended)

**Best for**: Multi-unit deployments with shared storage and automatic role assignment

**Shared Storage Benefits**:
- ✅ Leader downloads binaries once
- ✅ Workers detect and reuse existing binaries
- ✅ 62% reduction in disk usage (3 units: 3GB → 1.15GB)
- ✅ 63% faster upgrades (coordinated download)

### Step 2A: Deploy First Unit (Becomes Web/Leader)

```bash
# Deploy with shared storage (10GB minimum)
juju deploy concourse-ci-machine \
  --storage concourse-data=shared-nfs,10G \
  --config mode=auto

# Wait for deployment to complete
juju wait-for application concourse-ci-machine --query='status=="active"'
```

**What happens**:
- Unit 0 becomes leader → runs web server
- Downloads Concourse binaries to shared `bin/`
- Writes `.installed_version` marker
- Starts `concourse-server.service`

**Verify**:
```bash
# Check unit status
juju status concourse-ci-machine

# View storage information
juju storage
# Look for concourse-data/0 attached to concourse-ci-machine/0

# SSH into unit and verify binaries
juju ssh concourse-ci-machine/0
ls -lh /var/lib/concourse/bin/
cat /var/lib/concourse/.installed_version
# Should show version like "7.14.3"
```

### Step 3A: Add Worker Units

```bash
# Add 2 worker units with shared storage
juju add-unit concourse-ci-machine \
  --attach-storage concourse-data/0 \
  --num-units 2

# Wait for units to join
juju wait-for application concourse-ci-machine --query='life=="alive"'
```

**What happens**:
- Units 1 and 2 are non-leaders → run workers only
- Detect existing `.installed_version` (no download!)
- Workers wait for binaries if download in progress
- Start `concourse-worker.service`

**Verify**:
```bash
# Check all units active
juju status concourse-ci-machine

# Verify storage is shared across units
juju storage
# Should show concourse-data/0 attached to multiple units

# Verify workers are NOT downloading binaries
juju ssh concourse-ci-machine/1 "ls -lh /var/lib/concourse/bin/"
# Same binaries from web/leader

juju ssh concourse-ci-machine/1 "ls -lh /var/lib/concourse/worker/concourse-ci-1/"
# Isolated worker state
```

## Deployment Scenario C: mode=web + mode=worker (Single Web, Multiple Workers)

**Best for**: Production deployments with dedicated web/worker separation

**⚠️ Current Limitation**: `mode=web` supports **single unit only**. Multiple web units for load balancing are **not yet implemented**.

### Step 2C: Deploy Single Dedicated Web Unit

```bash
# Deploy web-only unit with shared storage
juju deploy concourse-ci-machine web \
  --storage concourse-data=shared-nfs,10G \
  --config mode=web
```

**⚠️ DO NOT add more web units** (not yet supported):
```bash
# This is NOT YET supported (planned for future release)
juju add-unit web  # ❌ mode=web only supports single unit currently
```

### Step 3C: Deploy Multiple Dedicated Worker Units

```bash
# Deploy worker-only application
juju deploy concourse-ci-machine workers \
  --config mode=worker

# Attach shared storage to first worker
juju attach-storage workers/0 concourse-data/0

# Add more workers sharing the same storage
juju add-unit workers \
  --attach-storage concourse-data/0 \
  --num-units 2

# Connect workers to web via TSA relation
juju integrate web:web-tsa workers:worker-tsa
```

**What happens**:
- Web unit downloads binaries to shared `bin/`
- Worker units detect binaries via shared mount (no download!)
- Workers connect to web unit's TSA endpoint via relation

**Important**: In this mode, you MUST use relations for TSA connectivity:
```bash
juju integrate web:web-tsa workers:worker-tsa
```

## Step 4: Verify Shared Storage

Regardless of deployment mode, verify storage is shared:

```bash
# Check storage attachment
juju storage
# Should show concourse-data/0 attached to multiple units

# Verify same filesystem ID across units
juju ssh concourse-ci-machine/0 "df /var/lib/concourse | tail -1"
juju ssh concourse-ci-machine/1 "df /var/lib/concourse | tail -1"
# Device should be SAME

# Check binary location
juju ssh concourse-ci-machine/0 "ls -lh /var/lib/concourse/bin/"
juju ssh concourse-ci-machine/1 "ls -lh /var/lib/concourse/bin/"
# Should see same binaries

# Verify disk usage reduction
juju ssh concourse-ci-machine/0 "du -sh /var/lib/concourse/bin/"
# Total disk usage: ~1.15× binary size (not N× for N units!)
```

## Step 5: Test Coordinated Upgrade

Trigger an upgrade to see coordinated download:

```bash
# Step 1: Set desired version (source of truth)
juju config concourse-ci-machine version=7.14.4

# Step 2: Trigger the upgrade by setting config
juju config concourse-ci-machine version=7.14.4

# Watch upgrade coordination
juju debug-log --replay --include concourse-ci-machine
```

**Note**: Setting the config triggers the upgrade process automatically.

**Expected Flow**:
1. Web/leader sets `upgrade-state=prepare` in peer relation
2. Workers detect signal, stop `concourse-worker.service`
3. Workers acknowledge with `upgrade-ready=true`
4. Web/leader waits for all workers (2-minute timeout)
5. Web/leader downloads new binaries (once to shared storage!)
6. Web/leader restarts services
7. Web/leader sets `upgrade-state=complete`
8. Workers detect signal, start `concourse-worker.service`

**Verify**:
```bash
# Check all units running new version
juju ssh concourse-ci-machine/0 "cat /var/lib/concourse/.installed_version"
# Should show "7.14.4"

juju ssh concourse-ci-machine/1 "/var/lib/concourse/bin/concourse --version"
# Should show new version

# Verify config was updated
juju config concourse-ci-machine version
# Should show "7.14.4"
```

## Step 6: Access Concourse Web UI

### For mode=auto:
```bash
# Get web UI URL (leader unit)
juju status --format=json | jq -r '.applications."concourse-ci-machine".units."concourse-ci-machine/0".address'

# Get admin credentials
juju config concourse-ci-machine initial-admin-username  # Default: admin
juju run concourse-ci-machine/leader get-admin-password  # Get password
```

### For mode=web + mode=worker:
```bash
# Get web UI URL (web application)
juju status --format=json | jq -r '.applications."web".units."web/0".address'

# Get admin credentials
juju config web initial-admin-username  # Default: admin
juju run web/leader get-admin-password  # Get password
```

Open browser to `http://<web-address>:8080` and log in.

## Troubleshooting

### Issue: Workers Timeout Waiting for Binaries
**Symptoms**: 
```
WorkerUnit/1: Waiting for web/leader to download v7.14.3... (timeout in 4m30s)
```

**Resolution**:
1. Check web/leader logs: `juju debug-log --include concourse-ci-machine/0`
2. Verify web/leader has network access to download binaries
3. Check for stale lock: `juju ssh concourse-ci-machine/0 "ls -l /var/lib/concourse/.download_in_progress"`
4. Verify the correct unit is downloading (leader in `auto` mode, web unit in `web`/`worker` mode)

### Issue: Storage Not Shared
**Symptoms**: Each unit downloads binaries independently

**Resolution**:
```bash
# Verify storage attachment
juju storage --format=yaml

# Check that concourse-data/0 is attached to multiple units
juju show-storage concourse-data/0

# Check filesystem ID matches across units
juju ssh concourse-ci-machine/0 "df /var/lib/concourse | tail -1"
juju ssh concourse-ci-machine/1 "df /var/lib/concourse | tail -1"
# Device should be SAME across units
```

### Issue: Trying to Add Multiple Web Units (mode=web)
**Symptoms**: 
```
ERROR cannot add unit: mode=web only supports single unit
```

**Resolution**:
```bash
# Multiple web units not yet supported
# For multi-unit deployment, use mode=auto instead:

# Remove web-only deployment
juju remove-application web

# Deploy with mode=auto
juju deploy concourse-ci-machine \
  --storage concourse-data=shared-nfs,10G \
  --config mode=auto

# Add worker units
juju add-unit concourse-ci-machine --attach-storage concourse-data/0 --num-units 2
```

**Note**: Load balancing with multiple web units is planned for a future release.

### Issue: Workers Can't Connect to Web (mode=web + mode=worker)
**Symptoms**: Workers show "failed to connect to TSA"

**Resolution**:
```bash
# Verify TSA relation exists
juju status --relations
# Should show web:web-tsa <-> workers:worker-tsa

# Create relation if missing
juju integrate web:web-tsa workers:worker-tsa

# Check web unit TSA is listening
juju ssh web/0 "ss -tlnp | grep 2222"
```

### Issue: Upgrade Hangs at "Waiting for Workers"
**Symptoms**: Web/leader stuck waiting for worker acknowledgments

**Resolution**:
1. Check worker status: `juju status concourse-ci-machine/1`
2. Verify peer relation: `juju show-unit concourse-ci-machine/1 --format=json`
3. Check service status: `juju run concourse-ci-machine/1 check-status verbose=true`
4. Force timeout (2-minute default): Web/leader proceeds after timeout

### Issue: Permission Denied on Shared Storage
**Symptoms**: 
```
PermissionError: [Errno 13] Permission denied: '/var/lib/concourse/bin/concourse'
```

**Resolution**:
```bash
# Check mount permissions
juju ssh concourse-ci-machine/0 "ls -ld /var/lib/concourse/"

# NFS: Ensure no_root_squash is set
# On NFS server: /etc/exports should have:
# /exports/concourse-storage *(rw,sync,no_root_squash,no_subtree_check)
```

### Issue: Config vs Action Mismatch
(Deprecated: The upgrade action has been removed. Setting config is sufficient.)

## Performance Metrics

Expected performance after shared storage deployment:

| Metric | Without Shared Storage | With Shared Storage | Improvement |
|--------|------------------------|---------------------|-------------|
| Initial deployment (3 units) | ~15 minutes | ~7 minutes | 53% faster |
| Disk usage (3 units) | 3× binary size (~3GB) | 1.15× binary size (~1.15GB) | 62% reduction |
| Upgrade time (3 units) | ~8 minutes | ~3 minutes | 63% faster |
| Add new worker unit | ~5 minutes | <2 minutes | 60% faster |

## Advanced Configuration

### Configure Storage Pool Size
```bash
# Deploy with larger storage
juju deploy concourse-ci-machine \
  --storage concourse-data=shared-nfs,50G \
  --config mode=auto
```

### Configure Worker Settings
```bash
# Set worker processes and tags
juju config concourse-ci-machine worker-procs=4
juju config concourse-ci-machine tag="gpu,high-mem"
```

### Enable GPU Support
```bash
# Enable GPU on worker units (mode=auto or mode=worker)
juju config concourse-ci-machine enable-gpu=true
juju config concourse-ci-machine gpu-device-ids="0,1"
```

### Custom Web Port
```bash
# Change web UI port (supports privileged ports < 1024)
juju config concourse-ci-machine web-port=80
```

### Monitor Upgrade Coordination
```bash
# Enable debug logging
juju config concourse-ci-machine log-level=debug

# Check service status
juju run concourse-ci-machine/leader check-status verbose=true

# Manually check peer relation data
juju ssh concourse-ci-machine/0
sudo relation-get -r concourse-peer:0 - concourse-ci-machine/0
```

## Available Actions

The charm provides several actions for operational tasks:

```bash
# Check service status
juju run concourse-ci-machine/leader check-status verbose=true

# Restart services gracefully
juju run concourse-ci-machine/leader restart-services

# Force restart services
juju run concourse-ci-machine/leader restart-services force=true

# Get admin password
juju run concourse-ci-machine/leader get-admin-password

# Upgrade to specific version (requires config set first)
juju config concourse-ci-machine version=7.14.4
juju run concourse-ci-machine/leader upgrade version=7.14.4

# Run database migrations (if using PostgreSQL)
juju run concourse-ci-machine/leader run-migrations

# Target specific migration version
juju run concourse-ci-machine/leader run-migrations target-version=1234
```

## Mode Comparison Table

| Feature | mode=auto | mode=web | mode=worker |
|---------|-----------|----------|-------------|
| **Units Supported** | **Multiple** ✅ | Single only ⚠️ | **Multiple** ✅ |
| **Shared Storage** | ✅ Leader downloads | ✅ Web downloads | ✅ Detects existing |
| **Binary Download** | Once (by leader) | Once (by web) | Never (waits for web) |
| **TSA Connection** | Via peer relation | Provides endpoint | Via explicit relation |
| **Scaling** | Add units ✅ | ❌ Not yet | Add worker units ✅ |
| **Status** | **Recommended** ✅ | Single web only | Requires separate web |
| **Best For** | Multi-unit deployments | Dedicated web tier | Dedicated workers |
| **Future Plans** | Active development | Multi-unit planned | Active development |

## Next Steps

- **Scale out**: Add more workers with `juju add-unit --attach-storage concourse-data/0`
- **Database integration**: Connect PostgreSQL 16+ with `juju integrate concourse-ci-machine postgresql`
- **Monitoring**: Enable metrics with `juju config concourse-ci-machine enable-metrics=true` and integrate with Prometheus using the `monitoring` relation
- **GPU workers**: Enable GPU support with `juju config concourse-ci-machine enable-gpu=true`
- **Vault integration**: Configure Vault for credential management (see config.yaml for vault-* options)
- **Backup**: Set up regular snapshots of shared storage volume

## Configuration Reference

Key configuration options from config.yaml:

| Option | Default | Description |
|--------|---------|-------------|
| `mode` | `auto` | Deployment mode (auto/web/worker) |
| `version` | (latest) | Concourse version to deploy |
| `web-port` | `8080` | Web UI port |
| `worker-procs` | `1` | Number of worker processes |
| `log-level` | `info` | Logging level (debug/info/warn/error) |
| `enable-metrics` | `true` | Enable Prometheus metrics |
| `enable-gpu` | `false` | Enable GPU support for workers |
| `tag` | `""` | Comma-separated worker tags |
| `external-url` | (auto) | External URL for web UI |
| `max-concurrent-downloads` | `10` | Max concurrent resource downloads |
| `container-placement-strategy` | `volume-locality` | Container placement strategy |

For full configuration options, see `config.yaml`.

## Storage Reference

Storage configuration from metadata.yaml:

| Storage Name | Location | Minimum Size | Description |
|-------------|----------|--------------|-------------|
| `concourse-data` | `/var/lib/concourse` | 10G | Persistent storage for worker work directory and volumes |

## Peer Relation Schema

The `concourse-peer` relation is used for upgrade coordination:

**Web/Leader sets**:
- `upgrade-state`: `idle` \| `prepare` \| `downloading` \| `complete`
- `target-version`: Version being upgraded to
- `timestamp`: UTC timestamp of state change
- `expected-worker-count`: Number of workers expected to acknowledge

**Workers set**:
- `upgrade-ready`: `true` when worker has stopped services
- `timestamp`: UTC timestamp of acknowledgment

## Reference

- **Feature Specification**: `specs/001-shared-storage/spec.md`
- **Data Model**: `specs/001-shared-storage/data-model.md`
- **Storage Coordinator Contract**: `specs/001-shared-storage/contracts/storage_coordinator.py`
- **Upgrade Protocol Contract**: `specs/001-shared-storage/contracts/upgrade_protocol.py`
- **Research Findings**: `specs/001-shared-storage/research.md`
- **Constitution**: `.specify/memory/constitution.md`
- **Charm Metadata**: `metadata.yaml`
- **Configuration**: `config.yaml`
- **Actions**: `actions.yaml`

## Support

If you encounter issues not covered in this guide:
1. Check Juju logs: `juju debug-log --replay --include concourse-ci-machine`
2. Review charm status: `juju status --relations`
3. Check available actions: `juju actions concourse-ci-machine`
4. Verify storage: `juju storage` and `juju show-storage concourse-data/0`
5. File an issue with logs and `juju export-bundle` output

## Appendix: Command Reference

Common Juju commands for shared storage management:

```bash
# Storage operations
juju storage                              # List all storage
juju show-storage concourse-data/0        # Show storage details
juju attach-storage <unit> <storage-id>   # Attach existing storage
juju detach-storage <storage-id>          # Detach storage

# Unit operations
juju add-unit <app> --attach-storage <id> # Add unit with shared storage
juju remove-unit <unit>                   # Remove unit (keeps storage)

# Relation operations
juju integrate <app1> <app2>              # Create relation
juju show-unit <unit> --format=json       # View unit relation data

# Action operations (use /leader for web/leader unit)
juju run <app>/leader <action> [key=value]  # Run action on leader
juju run <app>/<unit-num> <action>          # Run action on specific unit
```
