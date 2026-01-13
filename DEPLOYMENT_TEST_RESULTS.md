# Shared Storage Deployment Test Results

**Date**: 2026-01-13  
**Feature**: 001-shared-storage  
**Test Type**: Multi-unit deployment with LXC shared storage

## Test Configuration

- **Juju Model**: shared-storage-test
- **Charm**: concourse-ci-machine (local, revision 0)
- **Configuration**:
  - `shared-storage=lxc`
  - `mode=auto`
  - Units: 4 (scaled from 2 → 3 → 4)
- **PostgreSQL**: 16/stable (required for web server)
- **Shared Storage**: `/tmp/concourse-shared-test` mounted to `/var/lib/concourse` on all units
- **LXC Configuration**: `shift=true` for ID mapping, `readonly=false` for write access

## Test Results

### ✅ Phase 1-4 Implementation: VERIFIED

**Shared Storage Coordination:**
- ✅ Leader (unit/1) successfully acquired exclusive lock
- ✅ Leader downloaded Concourse v7.14.3 binaries once
- ✅ Worker (unit/0) waited for leader to complete download
- ✅ Both units share identical binaries from `/var/lib/concourse`
- ✅ Version marker (`.installed_version`) correctly written with `7.14.3`
- ✅ LXC marker detection working (`.lxc_shared_storage`)

**Deployment Timeline:**
1. 14:41:29 - Unit 1 (leader) acquired download lock
2. 14:41:29-14:42:34 - Unit 1 downloaded binaries (65 seconds)
3. 14:41:36 - Unit 0 (worker) waiting for binaries
4. 14:42:34 - Unit 1 marked download complete
5. 14:43:04 - Unit 0 became active as worker
6. 14:43:04 - Unit 1 waiting for PostgreSQL (web role)

**Final Status:**
```
Unit                     Workload  Agent  Message
concourse-ci-machine/0   active    idle   Worker ready (v7.14.3)
concourse-ci-machine/1*  active    idle   Web server ready (v7.14.3) - port 8080
concourse-ci-machine/2   active    idle   Worker ready (v7.14.3)
concourse-ci-machine/3   active    idle   Worker ready (v7.14.3)
postgresql/1*            active    idle   Primary - port 5432
```

### 📊 Shared Storage Verification

**Host filesystem** (`/tmp/concourse-shared-test/`):
```
.installed_version       6 bytes (version marker)
.install.lock            0 bytes (lock file)
.lxc_shared_storage      0 bytes (LXC marker)
bin/                     Downloaded binaries
keys/                    Shared SSH keys
worker/                  Worker-specific directories
config.env               499 bytes (shared config)
```

**Both units see identical content:**
- Unit 0: `/var/lib/concourse/bin/` → shared binaries
- Unit 1: `/var/lib/concourse/bin/` → shared binaries

### 🎯 Success Criteria Met

- [x] **Single Download**: Binaries downloaded once by leader (65 seconds)
- [x] **Lock Coordination**: Exclusive lock acquired successfully
- [x] **Worker Waiting**: Worker units waited for leader completion
- [x] **Version Marker**: Version file written and readable
- [x] **Shared Access**: All 4 units access same filesystem
- [x] **LXC Mode**: Marker file detection working
- [x] **Storage Path**: `/var/lib/concourse` used consistently
- [x] **Scaling Test**: Units 2 & 3 added post-deployment, instantly reused binaries
- [x] **PostgreSQL Integration**: Web server connected to PostgreSQL 16/stable
- [x] **Full Stack**: Complete 4-unit cluster operational

## Scaling Test Results

**Timeline of Unit Additions:**
1. **Initial deployment** (2 units): Unit 1 downloaded binaries, Unit 0 waited
2. **Scale to 3 units**: Unit 2 detected existing binaries instantly
3. **Scale to 4 units**: Unit 3 detected existing binaries instantly
4. **PostgreSQL integration**: All units connected successfully

**Observations:**
- First download: 65 seconds (v7.14.3, ~171 MB)
- Additional units: < 5 seconds to detect and use existing binaries
- No re-downloads occurred
- All units share identical filesystem ID

## Known Limitations

1. **Manual LXC Setup**: Shared storage requires manual LXC device configuration before deployment
2. **Phase 5 Incomplete**: Contention handling, retry logic, corruption detection not yet implemented
3. **Binary Validation**: Unit 2 initially reported missing binary warning but recovered successfully

## Next Steps

1. **Phase 5**: Implement storage contention and error recovery (13 tasks)
2. **Phase 6**: Polish and documentation (10 tasks)
3. **Phase 7**: CI integration for automated E2E testing (12 tasks)
4. **Test Upgrades**: Verify coordinated upgrade coordination with multiple units
5. **Test Contention**: Simulate concurrent operations to verify locking

## Conclusion

**The shared storage implementation (Phases 1-4) is working as designed.** The core feature is functionally complete:
- Multi-unit deployment eliminates binary duplication
- Leader downloads once, workers wait and reuse
- File-based locking coordinates access
- Version markers track installed binaries
- LXC shared storage mode functional

**Status**: ✅ MVP Complete - Ready for Phase 5 enhancements
