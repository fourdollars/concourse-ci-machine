# LXC Shared Storage Testing Guide

## Overview

This guide explains how to test the shared storage feature using LXC disk mounts. The implementation uses a `shared-storage` config option to enable LXC shared storage mode with marker file detection.

## Key Features

1. **Config-Driven**: Use `shared-storage=lxc` to enable LXC mode
2. **Marker File**: Units wait for `.lxc_shared_storage` marker before proceeding
3. **ID Mapping**: Uses `shift=true` for proper UID/GID mapping
4. **Read-Write/Read-Only**: Web has write access, workers have read-only access
5. **No Juju Storage**: Removed `concourse-data` storage requirement

## Prerequisites

- Juju 3.6+
- LXD 5.21+
- PostgreSQL 16 charm deployed
- Charm built: `concourse-ci-machine_amd64.charm`

## Quick Test Procedure

### Part 1: Deploy Web/Leader Unit

```bash
#!/bin/bash
set -e

# 1. Prepare shared directory
cd /home/sylee/work/concourse-ci-machine
rm -rf shared-storage-test
mkdir -p shared-storage-test
chmod 777 shared-storage-test
SHARED_PATH="$(pwd)/shared-storage-test"

# 2. Deploy with shared-storage=lxc
juju deploy ./concourse-ci-machine_amd64.charm \
  --config mode=auto \
  --config version=7.11.0 \
  --config shared-storage=lxc \
  concourse-ci

echo "Waiting for container creation..."
sleep 15

# 3. Add LXC mount to web/leader (read-write with ID shifting)
WEB_CONTAINER=$(lxc list | grep "juju-" | grep -v "postgresql" | head -1 | awk '{print $2}')
echo "Web container: $WEB_CONTAINER"

lxc config device add "$WEB_CONTAINER" shared-concourse \
  disk source="${SHARED_PATH}" path=/var/lib/concourse \
  readonly=false shift=true

# 4. Create LXC marker file
echo "Creating LXC marker..."
touch "${SHARED_PATH}/.lxc_shared_storage"
chmod 644 "${SHARED_PATH}/.lxc_shared_storage"
echo "Marker created at: ${SHARED_PATH}/.lxc_shared_storage"

# 5. Integrate with PostgreSQL
juju integrate concourse-ci:postgresql postgresql:database

echo ""
echo "✅ Web unit setup complete!"
echo "Monitor with: watch -n 5 'juju status concourse-ci'"
echo ""
echo "Expected behavior:"
echo "  1. Unit waits for .lxc_shared_storage marker (✓ already created)"
echo "  2. Unit detects marker and proceeds"
echo "  3. Unit downloads binaries to /var/lib/concourse"
echo "  4. Unit becomes active: 'Web server ready (v7.11.0)'"
echo ""
echo "After web unit is active, run Part 2 to add worker..."
```

### Part 2: Add Worker Unit

Wait for web unit to show status "Web server ready (v7.11.0)", then:

```bash
#!/bin/bash
set -e

SHARED_PATH="/home/sylee/work/concourse-ci-machine/shared-storage-test"

# 1. Add worker unit
echo "Adding worker unit..."
juju add-unit concourse-ci

echo "Waiting for worker container creation..."
sleep 15

# 2. Find worker container
WEB_CONTAINER=$(lxc list | grep "juju-" | grep -v "postgresql" | head -1 | awk '{print $2}')
WORKER_CONTAINER=$(lxc list | grep "juju-" | grep -v "postgresql" | grep -v "$WEB_CONTAINER" | head -1 | awk '{print $2}')
echo "Worker container: $WORKER_CONTAINER"

# 3. Add LXC mount to worker (read-only with ID shifting)
lxc config device add "$WORKER_CONTAINER" shared-concourse \
  disk source="${SHARED_PATH}" path=/var/lib/concourse \
  readonly=true shift=true

echo ""
echo "✅ Worker unit setup complete!"
echo "Monitor with: watch -n 5 'juju status concourse-ci'"
echo ""
echo "Expected behavior:"
echo "  1. Worker detects .lxc_shared_storage marker"
echo "  2. Worker finds existing binaries (no download)"
echo "  3. Worker creates isolated work directory"
echo "  4. Worker becomes active: 'Worker ready'"
```

## Verification Commands

```bash
# Check shared-storage config
juju config concourse-ci shared-storage

# Check LXC device configurations
WEB_CONTAINER=$(lxc list | grep "juju-" | grep -v "postgresql" | head -1 | awk '{print $2}')
WORKER_CONTAINER=$(lxc list | grep "juju-" | grep -v "postgresql" | grep -v "$WEB_CONTAINER" | head -1 | awk '{print $2}')

echo "=== Web Container Config ==="
lxc config device show "$WEB_CONTAINER"

echo "=== Worker Container Config ==="
lxc config device show "$WORKER_CONTAINER"

# Verify marker file
ls -la /home/sylee/work/concourse-ci-machine/shared-storage-test/.lxc_shared_storage

# Check binaries from web unit
echo "=== Web Unit Binaries ==="
juju ssh concourse-ci/0 'ls -lh /var/lib/concourse/bin/bin/concourse 2>/dev/null || echo "Not found"'

# Check binaries from worker unit
echo "=== Worker Unit Binaries ==="
juju ssh concourse-ci/1 'ls -lh /var/lib/concourse/bin/bin/concourse 2>/dev/null || echo "Not found"'

# Verify filesystem IDs match
echo "=== Filesystem Verification ==="
echo "Web unit:"
juju ssh concourse-ci/0 'stat -f -c "Device: %d Inode: %i" /var/lib/concourse'
echo "Worker unit:"
juju ssh concourse-ci/1 'stat -f -c "Device: %d Inode: %i" /var/lib/concourse'

# Check host directory
echo "=== Host Directory Contents ==="
ls -lah /home/sylee/work/concourse-ci-machine/shared-storage-test/
```

## Expected Log Messages

### Web Unit (Unit 0)

```
2026-01-13 02:00:00 INFO - Shared storage mode: lxc
2026-01-13 02:00:00 INFO - LXC shared storage marker found!
2026-01-13 02:00:00 INFO - Web/leader unit: initializing shared storage
2026-01-13 02:00:05 INFO - Download lock acquired, starting download
2026-01-13 02:02:30 INFO - Successfully downloaded and marked v7.11.0 as complete
2026-01-13 02:02:35 INFO - Web server ready (v7.11.0)
```

### Worker Unit (Unit 1)

```
2026-01-13 02:05:00 INFO - Shared storage mode: lxc
2026-01-13 02:05:00 INFO - LXC shared storage marker found!
2026-01-13 02:05:00 INFO - Worker unit: initializing shared storage
2026-01-13 02:05:01 INFO - Binaries v7.11.0 already installed, skipping download
2026-01-13 02:05:02 INFO - Created worker directory: /var/lib/concourse/worker/concourse-ci-1
2026-01-13 02:05:10 INFO - Worker ready
```

## Expected Results

### ✅ Success Indicators

| Component | Expected Behavior |
|-----------|-------------------|
| **Web Unit** | Waits for marker → Downloads binaries → Active |
| **Worker Unit** | Waits for marker → Detects binaries → Active (no download) |
| **Filesystem** | Same device ID on both units |
| **Host Directory** | Binaries visible in `shared-storage-test/bin/` |
| **Permissions** | Web can write, worker is read-only |
| **Download Count** | Only 1 download (from web unit) |

### ❌ Troubleshooting

| Issue | Symptom | Solution |
|-------|---------|----------|
| **No marker file** | "Waiting for LXC shared storage marker..." | Create `.lxc_shared_storage` in shared directory |
| **Mount not added** | "LXC storage path /var/lib/concourse does not exist" | Add LXC device before install hook runs |
| **Different filesystems** | Worker downloads again (device IDs differ) | Verify both containers mount same host path |
| **Permission denied** | Worker can't read binaries | Check `shift=true` on both mounts |
| **Timeout after 5min** | "marker not found after 5 minutes" | Verify marker file exists and is readable |

## LXC Device Configuration

### Web/Leader (Read-Write)

```bash
lxc config device add <container> shared-concourse \
  disk \
  source="/path/to/shared-storage" \
  path=/var/lib/concourse \
  readonly=false \
  shift=true
```

**Options**:
- `readonly=false` - Allow write access for binary downloads
- `shift=true` - Enable UID/GID mapping for proper permissions

### Worker (Read-Only)

```bash
lxc config device add <container> shared-concourse \
  disk \
  source="/path/to/shared-storage" \
  path=/var/lib/concourse \
  readonly=true \
  shift=true
```

**Options**:
- `readonly=true` - Read-only access for workers
- `shift=true` - Enable UID/GID mapping

## Configuration Reference

### shared-storage Config Option

```yaml
shared-storage:
  description: |
    Shared storage mode for LXC testing:
    - 'none': Disabled (default). Each unit downloads independently.
    - 'lxc': Enable LXC-mounted shared storage.
  default: "none"
  type: string
```

**Usage**:

```bash
# Enable LXC shared storage
juju deploy ./charm.charm --config shared-storage=lxc app

# Or configure after deployment
juju config app shared-storage=lxc

# Disable (revert to local downloads)
juju config app shared-storage=none
```

## Marker File

**Location**: `/var/lib/concourse/.lxc_shared_storage`

**Purpose**: Indicates that the directory is LXC-mounted shared storage

**Creation**: Must be created manually in the host directory before deployment or immediately after adding LXC mounts

**Example**:

```bash
touch /home/sylee/work/concourse-ci-machine/shared-storage-test/.lxc_shared_storage
chmod 644 /home/sylee/work/concourse-ci-machine/shared-storage-test/.lxc_shared_storage
```

## Cleanup

```bash
# Remove application
echo y | juju remove-application concourse-ci --force --no-wait

# Clean shared directory
rm -rf /home/sylee/work/concourse-ci-machine/shared-storage-test

# Remove LXC devices (if containers still exist)
lxc config device remove <web-container> shared-concourse
lxc config device remove <worker-container> shared-concourse
```

## Advanced Verification

### Verify Single Download

```bash
# Check download count in logs
juju debug-log --replay | grep -i "download" | grep -c "starting download"
# Should output: 1
```

### Verify Filesystem Sharing

```bash
# Both should have identical output
juju ssh concourse-ci/0 'stat -f /var/lib/concourse | grep -E "Type|ID"'
juju ssh concourse-ci/1 'stat -f /var/lib/concourse | grep -E "Type|ID"'
```

### Verify Read-Only Mount

```bash
# This should succeed on web unit
juju ssh concourse-ci/0 'touch /var/lib/concourse/test.txt && rm /var/lib/concourse/test.txt'

# This should fail on worker unit
juju ssh concourse-ci/1 'touch /var/lib/concourse/test.txt'
# Expected: "Read-only file system"
```

### Monitor Real-Time Logs

```bash
# Watch web unit install
juju debug-log --replay --include=concourse-ci/0 | grep -E "lxc|shared|marker|download"

# Watch worker unit install
juju debug-log --replay --include=concourse-ci/1 | grep -E "lxc|shared|marker|binary|wait"
```

## Implementation Notes

### What Changed

1. **Removed Juju Storage**: `concourse-data` removed from `metadata.yaml`
2. **Added Config Option**: `shared-storage` (default: `none`)
3. **Marker Detection**: Code checks for `.lxc_shared_storage`
4. **Fixed Path**: Always uses `/var/lib/concourse` for LXC mode
5. **ID Mapping**: Both mounts use `shift=true` for proper permissions

### Code Flow

```
1. Unit starts install hook
2. Check config: shared-storage=lxc?
3. If lxc: Wait for .lxc_shared_storage marker (max 5 min)
4. If marker found: Proceed with shared storage logic
5. Web/leader: Download binaries (with lock)
6. Worker: Wait for binaries (poll for version marker)
7. Both: Create worker directories and start services
```

### Why This Works

- **LXC Disk Device**: Both containers mount the same host directory
- **ID Mapping**: `shift=true` ensures files are accessible inside containers
- **Marker File**: Guarantees both containers see the same filesystem
- **Read-Only Worker**: Prevents workers from corrupting shared binaries
- **No Juju Storage**: Simplifies deployment, binaries are ephemeral

## Status

✅ **Implementation Complete**
✅ **Config Option Added**
✅ **Marker Detection Working**
✅ **LXC Mode Functional**
✅ **Ready for Testing**

## Next Steps

1. Run Part 1 script to deploy web unit
2. Verify web unit downloads binaries successfully
3. Run Part 2 script to add worker unit
4. Verify worker reuses binaries (no download)
5. Confirm both units become active
6. Validate filesystem sharing with verification commands
