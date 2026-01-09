# Feature Specification: Shared Storage for Concourse CI Units

**Feature Branch**: `001-shared-storage`  
**Created**: 2026-01-09  
**Status**: Draft  
**Input**: User description: "Reuse the same concourse-data storage for all Concourse CI units to reduce the duplicated binary downloads and minimize disk space usage."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy Multi-Unit Concourse Without Storage Duplication (Priority: P1)

As a Juju operator, when I deploy Concourse CI with multiple units, I want all units to share the same storage volume for Concourse binaries and worker data so that disk space is not wasted with duplicate downloads and each unit doesn't maintain its own copy of the same binaries.

**Why this priority**: This is the core value proposition - reducing storage overhead in multi-unit deployments. Without this, operators face excessive disk usage costs and slower deployments due to redundant binary downloads.

**Independent Test**: Deploy a 3-unit Concourse CI charm in auto mode. Verify that all units reference the same storage volume and that Concourse binaries are downloaded only once. Measure total disk usage and confirm it's approximately 1x binary size rather than 3x.

**Acceptance Scenarios**:

1. **Given** Concourse CI charm deployed with 3 units, **When** checking storage mounts across all units, **Then** all units should mount the same shared storage volume
2. **Given** shared storage volume already contains Concourse binaries, **When** adding a new unit, **Then** the new unit should use existing binaries without re-downloading
3. **Given** multiple units sharing storage, **When** checking disk usage, **Then** total storage consumption should be ~1x binary size plus per-unit work directories (not N x binary size)

---

### User Story 2 - Upgrade Concourse with Shared Storage (Priority: P2)

As a Juju operator, when I upgrade Concourse CI version across multiple units, I want the new binaries downloaded once to shared storage so that the upgrade completes faster and uses minimal bandwidth.

**Why this priority**: Upgrades are common operational tasks. Shared storage significantly reduces upgrade time and bandwidth consumption, especially in bandwidth-constrained environments.

**Independent Test**: Deploy multi-unit Concourse, then run upgrade action from v7.14.2 to v7.14.3. Verify binaries are downloaded once to shared storage and all units reference the upgraded version.

**Acceptance Scenarios**:

1. **Given** multi-unit Concourse deployment with shared storage, **When** upgrade action is triggered on leader, **Then** new version binaries are downloaded once to shared storage
2. **Given** new binaries in shared storage, **When** worker units upgrade, **Then** they use the shared binaries without additional downloads
3. **Given** upgrade in progress, **When** checking download logs, **Then** only one download operation should occur regardless of unit count

---

### User Story 3 - Handle Storage Contention and Locking (Priority: P3)

As a Juju operator, when multiple units access shared storage concurrently, I want the charm to handle file locking correctly so that concurrent operations don't corrupt the shared storage or cause race conditions.

**Why this priority**: While less common than basic deployment, proper locking prevents edge case failures. This is a reliability enhancement rather than core functionality.

**Independent Test**: Deploy multi-unit Concourse. Simultaneously trigger config changes on multiple units that write to shared storage. Verify no corruption or lock conflicts occur.

**Acceptance Scenarios**:

1. **Given** multiple units writing to shared storage simultaneously, **When** operations complete, **Then** no file corruption or incomplete writes should occur
2. **Given** one unit performing upgrade (writing new binaries), **When** another unit starts up, **Then** the starting unit should wait for write operations to complete before reading
3. **Given** shared storage under concurrent access, **When** checking system logs, **Then** no file lock timeout errors should appear

---

### Edge Cases

- What happens when shared storage volume becomes unavailable while units are running?
- How does the system handle disk space exhaustion on the shared volume?
- What happens if a unit attempts to modify binaries in shared storage while other units are using them?
- How does the charm handle storage migration or relocation of the shared volume?
- What happens during rollback if new version binaries are in shared storage but old version needs to be restored?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Charm MUST configure Juju storage such that multiple units can mount the same concourse-data volume
- **FR-002**: Charm MUST download Concourse binaries to shared storage on first deployment
- **FR-003**: Subsequent units MUST detect existing binaries in shared storage and skip downloads
- **FR-004**: Charm MUST implement file locking mechanisms to prevent concurrent write conflicts
- **FR-005**: Upgrade action MUST download new binaries to shared storage once, not per-unit
- **FR-006**: Worker units MUST reference binaries from shared storage rather than maintaining per-unit copies
- **FR-007**: Charm MUST handle storage unavailability gracefully with appropriate error messages
- **FR-008**: Each unit MUST maintain its own worker-specific data directory (separate from shared binaries)
- **FR-009**: Charm MUST log storage access patterns for debugging (which unit downloaded, which units read)
- **FR-010**: Removal of units MUST NOT affect shared storage or remaining units

### Key Entities

- **Shared Storage Volume**: Juju storage resource mounted by all Concourse units, contains Concourse binaries and common data
- **Binary Directory**: Path within shared storage containing Concourse executables (concourse, fly, etc.)
- **Worker Data Directory**: Per-unit directory for worker-specific runtime data (containers, volumes, state)
- **Lock File**: Coordination mechanism to prevent concurrent writes to shared binaries

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Multi-unit deployment (3+ units) uses <20% more disk space than single-unit deployment (target: ~1.15x rather than 3x)
- **SC-002**: Adding a new unit to existing deployment completes in <3 minutes when binaries already exist in shared storage (vs ~5 minutes with download)
- **SC-003**: Upgrade operation across 5 units downloads binaries exactly once (verified via network traffic logs)
- **SC-004**: Concurrent unit operations (startup, config-change) complete without storage-related errors in 99% of cases
- **SC-005**: Storage failure scenario provides clear operator guidance within 30 seconds of detection

### Assumptions

- Juju supports shared storage volumes across multiple units (storage can be mounted in shared mode)
- Filesystem on shared storage supports POSIX file locking or equivalent coordination mechanism
- Network latency to shared storage is acceptable (<10ms for typical read operations)
- Operators have sufficient disk space on shared volume for N+1 version storage (current + upgrade)
- Shared storage backend provides sufficient IOPS for concurrent reads from multiple units

### Out of Scope

- Distributed locking systems (Redis, etcd) - using filesystem-based locking only
- Storage replication or high-availability configuration (relies on Juju storage provider)
- Dynamic storage resizing or automatic cleanup of old versions
- Storage performance optimization or caching layers
