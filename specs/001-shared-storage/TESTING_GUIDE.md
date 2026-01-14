# Quick Testing Guide - Shared Storage Feature

## Prerequisites

- Juju 3.6+ installed
- LXD 5.21+ installed
- Charm built: `concourse-ci-machine_amd64.charm`
- Juju model created (e.g., `shared-storage-test`)

## Test 1: Local Fallback Mode (No Shared Storage)

**Purpose**: Verify charm works without shared storage (backward compatibility)

```bash
# Switch to test model
juju switch shared-storage-test

# Deploy single unit
juju deploy ./concourse-ci-machine_amd64.charm \
  --config mode=auto \
  --config version=7.11.0 \
  concourse-ci

# Monitor deployment
juju status --watch 5s

# Check logs
juju debug-log --replay --include=concourse-ci/0 | grep -E "storage|install|download"

# Expected Results:
# - Status: "No shared storage, using local installation"
# - Binaries installed to /opt/concourse/bin/
# - No storage coordinator initialization
# - Normal Concourse CI installation
```

### Validation

```bash
# SSH into unit
juju ssh concourse-ci/0

# Check binary location
ls -lh /opt/concourse/bin/concourse

# Check for storage coordinator usage (should be minimal/fallback)
sudo grep -i "shared storage" /var/log/concourse-ci.log

# Exit
exit
```

## Test 2: Shared Storage Mode - Auto Deployment

**Purpose**: Verify single download across multiple units

### Step 1: Clean Environment

```bash
# Remove previous deployment if exists
juju remove-application concourse-ci --force --no-wait

# Wait for cleanup
sleep 20
juju status
```

### Step 2: Deploy with Shared Storage

**Note**: Juju's `--attach-storage` requires existing storage. For testing, we'll use storage that Juju auto-creates.

```bash
# Deploy leader/web unit
juju deploy ./concourse-ci-machine_amd64.charm \
  --config mode=auto \
  --config version=7.11.0 \
  concourse-ci

# Wait for unit 0 to complete installation
juju status --watch 5s
# Wait until unit 0 is active/idle

# Add worker units
juju add-unit concourse-ci
juju add-unit concourse-ci

# Monitor status
juju status --watch 5s
```

### Step 3: Verify Shared Storage Behavior

```bash
# Check logs for storage coordination
juju debug-log --replay --limit 200 | grep -E "storage|lock|download|wait"

# Expected log entries:
# - Unit 0: "Web/leader unit: initializing shared storage"
# - Unit 0: "Download lock acquired"
# - Unit 0: "Successfully downloaded and marked v7.11.0 as complete"
# - Units 1-2: "Worker unit: initializing shared storage"
# - Units 1-2: "Waiting for web/leader to download"
# - Units 1-2: "Binaries v7.11.0 already available"
```

### Step 4: Verify Binary Locations

```bash
# Check unit 0 (leader/web)
juju ssh concourse-ci/0 "ls -lh /opt/concourse/bin/ && df -h /var/lib/concourse"

# Check unit 1 (worker)
juju ssh concourse-ci/1 "ls -lh /opt/concourse/bin/ && df -h /var/lib/concourse"

# Check unit 2 (worker)
juju ssh concourse-ci/2 "ls -lh /opt/concourse/bin/ && df -h /var/lib/concourse"

# Expected: Same binary locations if shared storage is working
# If local fallback: Each unit has independent /opt/concourse/bin/
```

### Step 5: Verify Worker Isolation

```bash
# Check worker directories
juju ssh concourse-ci/1 "sudo ls -la /var/lib/concourse/worker/"
juju ssh concourse-ci/2 "sudo ls -la /var/lib/concourse/worker/"

# Expected (if shared storage):
# - /var/lib/concourse/worker/concourse-ci-1/
# - /var/lib/concourse/worker/concourse-ci-2/
# Each with own work_dir and state.json
```

## Test 3: Measure Disk Usage

**Purpose**: Verify <1.2× disk usage target

```bash
# Get binary size
juju ssh concourse-ci/0 "du -sh /opt/concourse"

# Get total usage across units
for unit in 0 1 2; do
  echo "=== Unit $unit ==="
  juju ssh concourse-ci/$unit "df -h /opt/concourse /var/lib/concourse"
done

# Calculate total usage
# Expected (shared storage): ~1.15× binary size
# Expected (local fallback): 3× binary size
```

## Test 4: Check Status Messages

```bash
# View current status
juju status

# Expected status messages during deployment:
# - "Installing Concourse CI..."
# - "Downloading Concourse v7.11.0..." (unit 0 only)
# - "Waiting for binaries v7.11.0..." (units 1-2)
# - "Binaries ready"
# - "Installation complete"
# - Eventually: "active" workload status
```

## Test 5: Verify Lock Behavior

```bash
# Check for lock files (if shared storage active)
juju ssh concourse-ci/0 "sudo ls -la /var/lib/concourse/.download_lock*"

# Check logs for lock acquisition
juju debug-log --replay | grep -E "lock acquired|lock released|Download lock"

# Expected:
# - "Download lock acquired" (unit 0)
# - "Lock released" (unit 0)
# - No lock errors
```

## Test 6: Add New Unit (Dynamic Scaling)

**Purpose**: Verify new units can join and reuse existing binaries

```bash
# Add a 4th unit
juju add-unit concourse-ci

# Monitor its installation
juju debug-log --include=concourse-ci/3 | grep -E "storage|download|wait"

# Expected:
# - "Worker unit: initializing shared storage"
# - "Binaries v7.11.0 already available" (no download)
# - Faster installation (~2-3 min vs 5+ min)
```

## Troubleshooting

### Issue: Storage Not Detected

```bash
# Check storage attachment
juju storage --format=yaml

# Check storage-get availability
juju ssh concourse-ci/0 "which storage-get"

# Check logs for storage path
juju debug-log --replay | grep "storage-get\|Storage.*mounted"
```

### Issue: Hook Failures

```bash
# Check hook status
juju status

# View detailed logs
juju debug-log --replay --include=concourse-ci/0 | tail -100

# Resolve hook errors
juju resolve concourse-ci/0

# Or re-run failed hook
juju run concourse-ci/0 hooks/install
```

### Issue: Download Timeout

```bash
# Check if unit is stuck waiting
juju debug-log --include=concourse-ci/1 | grep -E "Waiting|timeout"

# Check if leader completed download
juju ssh concourse-ci/0 "sudo cat /var/lib/concourse/bin/.version_marker"

# Force retry on worker
juju resolve concourse-ci/1
```

## Success Criteria Checklist

- [ ] Charm deploys without errors
- [ ] All units reach active/idle status
- [ ] Web server accessible (check juju status for IP:8080)
- [ ] Workers register with TSA (check concourse web UI)
- [ ] Logs show appropriate storage coordination messages
- [ ] Binary locations are correct
- [ ] Worker directories are isolated
- [ ] Disk usage meets target (<1.2× if shared storage)
- [ ] New units can join without re-downloading
- [ ] No lock errors or timeouts in logs

## Cleanup

```bash
# Remove application
juju remove-application concourse-ci --force --no-wait

# Destroy test model (optional)
juju destroy-model shared-storage-test --destroy-storage --force

# Rebuild charm if needed
cd /home/sylee/work/concourse-ci-machine
charmcraft pack
```

## Expected Timeline

- **Test 1** (Local fallback): ~10 minutes
- **Test 2** (Shared storage): ~15 minutes
- **Test 3-6** (Validation): ~10 minutes
- **Total**: ~35-40 minutes for complete testing

## Notes

- Storage sharing requires proper Juju storage configuration
- Without attached storage, charm uses local fallback mode
- LXD storage pools can be used for testing
- For production, use NFS or Ceph storage
- Monitor `juju debug-log` continuously during tests for real-time feedback
