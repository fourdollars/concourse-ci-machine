# Strict Shared Storage Mode - Implementation Summary

## Changes Made

### 1. **lib/concourse_web.py** - Web/Leader Storage Enforcement
**Before:** Returned `None` if `/var/lib/concourse` doesn't exist (silent fallback)
**After:** Raises `StorageNotMountedError` with clear error message

```python
if not storage_path.exists():
    raise StorageNotMountedError(
        f"Shared storage mode 'lxc' requires {storage_path} to exist. "
        f"Please ensure the LXC container has shared storage properly mounted. "
        f"See documentation for LXC shared directory configuration."
    )
```

### 2. **lib/concourse_worker.py** - Worker Storage Enforcement  
**Before:** Returned `None` if `/var/lib/concourse` doesn't exist (silent fallback)
**After:** Raises `StorageNotMountedError` with detailed error message

```python
if not storage_path.exists():
    raise StorageNotMountedError(
        f"Shared storage mode 'lxc' requires {storage_path} to exist. "
        f"Worker unit cannot proceed without shared storage access. "
        f"Please ensure the LXC container has shared storage properly mounted. "
        f"See documentation for LXC shared directory configuration."
    )
```

### 3. **src/charm.py** - Install Hook Error Handling
**Before:** Silent fallback to local installation when storage_coordinator is None
**After:** Explicit error when shared-storage is configured but unavailable

```python
elif self.config.get("shared-storage", "none") != "none":
    raise Exception(
        f"Shared storage mode '{self.config['shared-storage']}' is configured "
        "but shared storage initialization failed. Check logs for details."
    )
else:
    # Only fallback when shared-storage=none explicitly
    logger.info("No shared storage configured, using local installation")
    download_and_install_concourse(self, version)
```

## Behavior Changes

### Configuration: `shared-storage=none` (Default)
- ✅ **Web units**: Install binaries locally in `/opt/concourse/bin/`
- ✅ **Worker units**: Install binaries locally in `/opt/concourse/bin/`
- ✅ **No shared storage required**
- ✅ **Compatible with existing deployments**

### Configuration: `shared-storage=lxc` (Strict Mode)
- ❌ **Web units**: **FAIL** if `/var/lib/concourse` doesn't exist
- ❌ **Worker units**: **FAIL** if `/var/lib/concourse` doesn't exist  
- ✅ **Error message**: Clear instructions on what's missing
- ✅ **No silent fallback**: Forces proper configuration

## Testing the Changes

### Test 1: Deployment Without Shared Storage (Should Fail)
```bash
juju deploy ./concourse-ci-machine_amd64.charm --config shared-storage=lxc

# Expected: Install hook FAILS with StorageNotMountedError
# Message: "Shared storage mode 'lxc' requires /var/lib/concourse to exist..."
```

### Test 2: Deployment With Proper Shared Storage (Should Succeed)
```bash
# 1. Create shared directory on host
mkdir -p /tmp/concourse-shared

# 2. Configure LXC containers with shared disk
lxc config device add juju-XXXXX-0 shared-concourse disk \
    source=/tmp/concourse-shared \
    path=/var/lib/concourse

lxc config device add juju-XXXXX-1 shared-concourse disk \
    source=/tmp/concourse-shared \
    path=/var/lib/concourse

# 3. Deploy charm
juju deploy ./concourse-ci-machine_amd64.charm --config shared-storage=lxc
juju add-unit concourse-ci-machine

# Expected: 
# - Unit 0 (web): Downloads binaries to /var/lib/concourse/bin/
# - Unit 1 (worker): Detects existing binaries, skips download
# - Both units share the same storage
```

### Test 3: Default Behavior (Should Work as Before)
```bash
juju deploy ./concourse-ci-machine_amd64.charm
# OR
juju deploy ./concourse-ci-machine_amd64.charm --config shared-storage=none

# Expected: Works exactly as before (local installation)
```

## Error Messages Users Will See

### When shared-storage=lxc But Not Mounted

**Web Unit:**
```
ERROR StorageNotMountedError: Shared storage mode 'lxc' requires /var/lib/concourse to exist. Please ensure the LXC container has shared storage properly mounted. See documentation for LXC shared directory configuration.
```

**Worker Unit:**
```
ERROR StorageNotMountedError: Shared storage mode 'lxc' requires /var/lib/concourse to exist. Worker unit cannot proceed without shared storage access. Please ensure the LXC container has shared storage properly mounted. See documentation for LXC shared directory configuration.
```

## Benefits

1. **No Silent Failures**: Admins know immediately if shared storage isn't configured
2. **Clear Error Messages**: Actionable guidance on what to fix
3. **Backward Compatible**: Existing deployments with `shared-storage=none` unaffected
4. **Prevents Confusion**: No unexpected local downloads when shared storage was intended
5. **Easier Debugging**: Fails fast with clear error rather than subtle behavioral differences

## Next Steps

To complete the shared storage feature:
1. ✅ **Strict mode enforced** (this document)
2. ⏳ **Complete Phase 4 tasks** (T043-T053): Upgrade coordination
3. ⏳ **Implement Phase 5** (T054-T066): Lock contention handling
4. ⏳ **Add documentation** showing LXC shared directory setup
5. ⏳ **Add CI tests** validating strict mode behavior

## Related Files Modified

- `lib/concourse_web.py` - Web/leader storage initialization
- `lib/concourse_worker.py` - Worker storage initialization  
- `src/charm.py` - Install hook error handling

## Task Status Update

This change addresses the requirement from testing feedback:
- **User Request**: "no fallback"
- **Implementation**: Strict enforcement when `shared-storage=lxc`
- **Status**: ✅ Complete
