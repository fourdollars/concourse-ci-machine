---

description: "Task list for shared storage feature implementation"
---

# Tasks: Shared Storage for Concourse CI Units

**Input**: Design documents from `/specs/001-shared-storage/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Tests are NOT explicitly requested in the feature specification. This task list focuses on implementation only.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: Juju charm with modular `lib/` structure
- Paths follow existing charm structure: `src/`, `lib/`, `metadata.yaml`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure (no code changes required for this feature)

- [x] T001 Verify metadata.yaml storage configuration supports shared volumes (existing config at lines 72-77)
- [x] T002 [P] Verify Python 3.11+ environment and ops framework ≥2.0.0 dependencies in requirements.txt
- [x] T003 [P] Create lib/storage_coordinator.py module structure (empty skeleton with copyright header)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core storage coordination infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Implement SharedStorage dataclass in lib/storage_coordinator.py with volume_path, filesystem_id, bin_directory, keys_directory, lock_file_path fields per data-model.md lines 17-56
- [x] T005 [P] Implement LockCoordinator dataclass in lib/storage_coordinator.py with acquire_exclusive context manager using fcntl.flock per data-model.md lines 68-122
- [x] T006 [P] Implement UpgradeState dataclass in lib/storage_coordinator.py with to_relation_data/from_relation_data methods per data-model.md lines 129-176
- [x] T007 [P] Implement WorkerDirectory dataclass in lib/storage_coordinator.py with from_shared_storage factory method per data-model.md lines 184-230
- [x] T008 [P] Implement ServiceManager dataclass in lib/storage_coordinator.py with stop/start/restart methods using subprocess per data-model.md lines 235-300
- [x] T009 [P] Implement IStorageCoordinator interface methods in lib/storage_coordinator.py per contracts/storage_coordinator.py lines 42-181
- [x] T010 [P] Implement IProgressTracker interface methods in lib/storage_coordinator.py per contracts/storage_coordinator.py lines 184-233
- [x] T011 [P] Implement IFilesystemValidator interface methods in lib/storage_coordinator.py per contracts/storage_coordinator.py lines 236-282
- [x] T012 [P] Implement IUpgradeCoordinator interface methods in lib/storage_coordinator.py per contracts/upgrade_protocol.py lines 135-260
- [x] T013 [P] Implement IServiceManager interface methods in lib/storage_coordinator.py per contracts/upgrade_protocol.py lines 263-323
- [x] T014 [P] Implement IRelationDataAccessor interface methods in lib/storage_coordinator.py per contracts/upgrade_protocol.py lines 326-393
- [x] T015 [P] Define SharedStorageError exception hierarchy in lib/storage_coordinator.py per data-model.md lines 383-407
- [x] T016 [P] Add type hints and docstrings to all storage_coordinator.py functions per constitution code quality standards

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Deploy Multi-Unit Concourse Without Storage Duplication (Priority: P1) 🎯 MVP

**Goal**: Enable multiple Concourse units to share the same storage volume for binaries, reducing disk usage from N×binary size to ~1.15×binary size

**Independent Test**: Deploy a 3-unit Concourse CI charm in auto mode. Verify that all units reference the same storage volume and that Concourse binaries are downloaded only once. Measure total disk usage and confirm it's approximately 1x binary size rather than 3x.

### Implementation for User Story 1

- [x] T017 [P] [US1] Modify lib/concourse_installer.py to add detect_existing_binaries method that checks SharedStorage.version_marker_path before downloading
- [x] T018 [P] [US1] Modify lib/concourse_installer.py to add verify_binaries method that validates binary executability and version match
- [x] T019 [US1] Update lib/concourse_installer.py download_binaries method to use LockCoordinator.acquire_exclusive before download (web/leader only)
- [x] T020 [US1] Update lib/concourse_installer.py download_binaries method to write SharedStorage.version_marker_path after successful download
- [x] T021 [US1] Update lib/concourse_installer.py to create .download_in_progress marker at download start, remove on completion
- [x] T022 [P] [US1] Modify lib/concourse_web.py to initialize SharedStorage on install hook using storage-get command
- [x] T023 [P] [US1] Modify lib/concourse_worker.py to initialize SharedStorage on install hook and call wait_for_binaries if version marker absent
- [x] T024 [US1] Update lib/concourse_worker.py to create WorkerDirectory using WorkerDirectory.from_shared_storage method in install hook
- [x] T025 [US1] Update lib/concourse_worker.py to configure concourse-worker.service with worker-specific work_dir from WorkerDirectory.work_dir
- [x] T026 [US1] Modify src/charm.py _on_install hook to detect unit role (web/leader vs worker) and branch logic accordingly
- [x] T027 [US1] Update src/charm.py _on_install hook for web/leader path: acquire lock, download binaries, write marker, start server
- [x] T028 [US1] Update src/charm.py _on_install hook for worker path: check existing binaries or wait, create worker directory, start worker service
- [x] T029 [US1] Add storage coordination logging in lib/concourse_common.py with unit name prefix (e.g., "[concourse-ci/1] Waiting for binaries...")
- [x] T030 [US1] Implement stale lock detection in lib/storage_coordinator.py LockCoordinator._is_stale method checking .download_in_progress age >10 minutes
- [x] T031 [US1] Implement stale lock cleanup in lib/storage_coordinator.py LockCoordinator._clean_stale_markers method
- [x] T032 [US1] Update lib/concourse_common.py to add get_storage_path helper that returns Path from storage-get command output
- [x] T033 [US1] Add filesystem validation in lib/storage_coordinator.py to verify all units mount same filesystem_id
- [x] T034 [US1] Update src/charm.py to set charm status messages during storage operations ("Downloading binaries...", "Waiting for binaries...", "Binaries ready")

**Checkpoint**: At this point, User Story 1 should be fully functional - multi-unit deployment shares binaries without duplication

---

## Phase 4: User Story 2 - Upgrade Concourse with Shared Storage (Priority: P2)

**Goal**: Enable upgrades where new binaries are downloaded once to shared storage, reducing upgrade time and bandwidth consumption

**Independent Test**: Deploy multi-unit Concourse, then run upgrade action from v7.14.2 to v7.14.3. Verify binaries are downloaded once to shared storage and all units reference the upgraded version.

### Implementation for User Story 2

- [ ] T035 [P] [US2] Implement UpgradeCoordinator.initiate_upgrade method in lib/storage_coordinator.py that sets phase=PREPARE in peer relation data
- [ ] T036 [P] [US2] Implement UpgradeCoordinator.wait_for_workers_ready method in lib/storage_coordinator.py with 2-minute timeout and worker count polling
- [ ] T037 [P] [US2] Implement UpgradeCoordinator.mark_download_phase method in lib/storage_coordinator.py that sets phase=DOWNLOADING
- [ ] T038 [P] [US2] Implement UpgradeCoordinator.complete_upgrade method in lib/storage_coordinator.py that sets phase=COMPLETE
- [ ] T039 [P] [US2] Implement UpgradeCoordinator.reset_upgrade_state method in lib/storage_coordinator.py that sets phase=IDLE
- [ ] T040 [P] [US2] Implement UpgradeCoordinator.get_upgrade_state method in lib/storage_coordinator.py that reads from peer relation data
- [ ] T041 [P] [US2] Implement UpgradeCoordinator.handle_prepare_signal method in lib/storage_coordinator.py for worker units (stop service, set ready flag)
- [ ] T042 [P] [US2] Implement UpgradeCoordinator.handle_complete_signal method in lib/storage_coordinator.py for worker units (verify binaries, start service)
- [ ] T043 [US2] Add upgrade action handler in src/charm.py that creates upgrade_concourse method with version parameter
- [ ] T044 [US2] Update src/charm.py upgrade_concourse method to branch on unit role: web/leader orchestrates, workers respond to signals
- [ ] T045 [US2] Implement web/leader upgrade orchestration in src/charm.py: initiate → wait for workers → download → restart → complete
- [ ] T046 [US2] Add _on_peer_relation_changed handler in src/charm.py that detects upgrade signals and calls appropriate coordinator methods
- [ ] T047 [US2] Update lib/concourse_worker.py to add upgrade preparation logic: detect PREPARE signal → stop worker service → acknowledge
- [ ] T048 [US2] Update lib/concourse_worker.py to add upgrade completion logic: detect COMPLETE signal → verify binaries → start worker service
- [ ] T049 [US2] Add RelationDataAccessor implementation in lib/storage_coordinator.py using ops.framework relation data APIs
- [ ] T050 [US2] Update lib/concourse_web.py to add upgrade download logic: acquire lock → download new version → update marker → restart server
- [ ] T051 [US2] Add upgrade timeout handling in lib/storage_coordinator.py UpgradeCoordinator with UpgradeTimeoutError exception
- [ ] T052 [US2] Update lib/concourse_common.py to add logging for upgrade phases ("Upgrade initiated", "Waiting for workers", "Download complete")
- [ ] T053 [US2] Add upgrade status updates in src/charm.py to show progress ("Upgrading to v7.14.3...", "Waiting for 2 workers...", "Upgrade complete")

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently - upgrades are coordinated via peer relations

---

## Phase 5: User Story 3 - Handle Storage Contention and Locking (Priority: P3)

**Goal**: Ensure concurrent storage operations don't corrupt shared storage or cause race conditions through proper file locking

**Independent Test**: Deploy multi-unit Concourse. Simultaneously trigger config changes on multiple units that write to shared storage. Verify no corruption or lock conflicts occur.

### Implementation for User Story 3

- [ ] T054 [P] [US3] Add LockAcquireError exception handling in lib/concourse_installer.py download_binaries method with retry logic
- [ ] T055 [P] [US3] Add progress marker validation in lib/storage_coordinator.py ProgressTracker to detect concurrent download attempts
- [ ] T056 [US3] Implement exponential backoff in lib/storage_coordinator.py StorageCoordinator.wait_for_binaries method (5s → 10s → 20s intervals)
- [ ] T057 [US3] Add filesystem write verification in lib/storage_coordinator.py FilesystemValidator.is_writable method before downloads
- [ ] T058 [US3] Update lib/concourse_installer.py to add checksum verification after binary download to detect corruption
- [ ] T059 [US3] Add atomic file operations in lib/storage_coordinator.py for marker files using Path.write_text with temp file + rename pattern
- [ ] T060 [US3] Implement lock timeout handling in lib/storage_coordinator.py LockCoordinator with detailed error messages
- [ ] T061 [US3] Add concurrent operation logging in lib/concourse_common.py showing which units hold locks and when
- [ ] T062 [US3] Update src/charm.py to handle LockAcquireError gracefully with status message "Another unit downloading, waiting..."
- [ ] T063 [US3] Add storage availability checks in lib/storage_coordinator.py SharedStorage.__post_init__ method with StorageNotMountedError
- [ ] T064 [US3] Implement binary corruption detection in lib/concourse_installer.py verify_binaries method using file hash comparison
- [ ] T065 [US3] Add lock cleanup logic in lib/storage_coordinator.py for orphaned locks from crashed units
- [ ] T066 [US3] Update lib/concourse_worker.py to handle concurrent worker starts gracefully with worker directory existence checks

**Checkpoint**: All user stories should now be independently functional with proper contention handling

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T067 [P] Add comprehensive docstrings to all public methods in lib/storage_coordinator.py following Google style guide
- [ ] T068 [P] Update docs/shared-storage.md with architecture diagrams from data-model.md data flow sections
- [ ] T069 [P] Add logging statements at DEBUG level for all storage operations in lib/storage_coordinator.py
- [ ] T070 [P] Update quickstart.md deployment examples with actual CLI commands for shared storage testing
- [ ] T071 Code review and refactoring: ensure all methods have type hints and follow constitution standards
- [ ] T072 Verify all exception messages include actionable context per constitution UX principle
- [ ] T073 Run quickstart.md deployment guide validation with actual 3-unit deployment
- [ ] T074 [P] Add performance logging for download operations showing duration and bandwidth metrics
- [ ] T075 Security review: ensure file permissions on shared storage prevent unauthorized access
- [ ] T076 Update lib/concourse_common.py to add storage statistics helper (disk usage, binary count, unit count)

---

## Phase 7: GitHub Actions CI Integration

**Purpose**: Add comprehensive E2E tests for all deployment modes with shared storage

- [ ] T077 [P] Add test-shared-storage-auto job to .github/workflows/ci.yml for mode=auto with 3 units using --attach-storage
- [ ] T078 [P] Add test-shared-storage-all job to .github/workflows/ci.yml for mode=all with 2 units sharing storage
- [ ] T079 [P] Add test-shared-storage-web-worker job to .github/workflows/ci.yml for separate apps with shared storage
- [ ] T080 Update publish-charm job dependencies in .github/workflows/ci.yml to include new shared storage test jobs
- [ ] T081 Add storage verification checks in test-shared-storage-auto: filesystem ID consistency, single download verification
- [ ] T082 Add disk usage measurement in test-shared-storage-auto: verify <1.2x binary size across all units
- [ ] T083 Add upgrade testing in test-shared-storage-auto: verify coordinated upgrade with single binary download
- [ ] T084 Add storage attachment verification in test-shared-storage-all: verify both units share same storage volume
- [ ] T085 Add TSA relation verification in test-shared-storage-web-worker: verify workers connect with shared storage
- [ ] T086 Add concurrent operation test: simultaneously trigger config changes on multiple units
- [ ] T087 Add new unit addition test: deploy 2 units, add 3rd with existing binaries, verify <3min addition time
- [ ] T088 Document CI test matrix in plan.md showing coverage for all modes (auto, all, web+worker) with shared storage

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 6)**: Depends on all desired user stories being complete
- **CI Integration (Phase 7)**: Can start after US1 complete, finish after US3

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - Builds on US1 storage coordination but independently testable
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - Enhances US1 and US2 with contention handling but independently testable

### Within Each User Story

- Phase 2 foundational classes MUST be complete before any user story tasks
- Within US1: Storage initialization → binary detection → download coordination → worker coordination
- Within US2: Upgrade protocol → peer relation handlers → orchestration logic → worker response
- Within US3: Lock handling → error recovery → concurrent operation support
- All tasks within a phase can be worked on incrementally (no strict ordering within story)

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel (T002-T003)
- All Foundational tasks marked [P] can run in parallel (T005-T016 after T004)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- Within each user story, tasks marked [P] can run in parallel:
  - US1: T017-T018, T022-T023 can be parallel
  - US2: T035-T042 (all interface implementations) can be parallel
  - US3: T054-T055, T057-T061 can be parallel
- Polish phase tasks marked [P] can run in parallel (T067-T070, T074)
- CI integration tasks marked [P] can run in parallel (T077-T079)

---

## Parallel Example: User Story 1

```bash
# After Foundational phase complete, launch US1 tasks in parallel:

# Parallel group 1: Installer modifications (different functions)
Task: "Modify lib/concourse_installer.py to add detect_existing_binaries method" [T017]
Task: "Modify lib/concourse_installer.py to add verify_binaries method" [T018]

# Parallel group 2: Web and worker initialization (different files)
Task: "Modify lib/concourse_web.py to initialize SharedStorage on install hook" [T022]
Task: "Modify lib/concourse_worker.py to initialize SharedStorage on install hook" [T023]

# Sequential after above: charm.py integration depends on lib/ changes
Task: "Modify src/charm.py _on_install hook to detect unit role" [T026]
```

---

## Parallel Example: User Story 2

```bash
# After Foundational + US1 complete, launch US2 interface implementations in parallel:

Task: "Implement UpgradeCoordinator.initiate_upgrade method" [T035]
Task: "Implement UpgradeCoordinator.wait_for_workers_ready method" [T036]
Task: "Implement UpgradeCoordinator.mark_download_phase method" [T037]
Task: "Implement UpgradeCoordinator.complete_upgrade method" [T038]
Task: "Implement UpgradeCoordinator.reset_upgrade_state method" [T039]
Task: "Implement UpgradeCoordinator.get_upgrade_state method" [T040]
Task: "Implement UpgradeCoordinator.handle_prepare_signal method" [T041]
Task: "Implement UpgradeCoordinator.handle_complete_signal method" [T042]
```

---

## Parallel Example: CI Integration

```bash
# After US1-US3 complete, add all CI test jobs in parallel:

Task: "Add test-shared-storage-auto job to .github/workflows/ci.yml" [T077]
Task: "Add test-shared-storage-all job to .github/workflows/ci.yml" [T078]
Task: "Add test-shared-storage-web-worker job to .github/workflows/ci.yml" [T079]
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T016) - CRITICAL blocking phase
3. Complete Phase 3: User Story 1 (T017-T034)
4. **STOP and VALIDATE**: Test User Story 1 independently with 3-unit deployment
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational (T001-T016) → Foundation ready
2. Add User Story 1 (T017-T034) → Test independently → Deploy/Demo (MVP! - Multi-unit shared storage)
3. Add User Story 2 (T035-T053) → Test independently → Deploy/Demo (Coordinated upgrades)
4. Add User Story 3 (T054-T066) → Test independently → Deploy/Demo (Robust contention handling)
5. Add CI Integration (T077-T088) → Validate all modes in CI
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together (T001-T016)
2. Once Foundational is done:
   - Developer A: User Story 1 (T017-T034) - Core shared storage
   - Developer B: User Story 2 (T035-T053) - Upgrade coordination
   - Developer C: User Story 3 (T054-T066) - Contention handling
3. Stories complete and integrate independently
4. Team D: CI Integration (T077-T088) - Comprehensive testing

---

## Notes

- [P] tasks = different files/functions, no dependencies within phase
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- Tests are NOT included as they were not explicitly requested in spec.md
- All tasks reference specific file paths and line numbers from design documents
- Constitution compliance verified: type hints, specific exceptions, no hardcoded paths
- Performance targets: <3min unit addition (US1), <2min upgrade (US2), <99% error-free concurrent ops (US3)

---

## Task Summary

- **Total Tasks**: 88
- **Phase 1 (Setup)**: 3 tasks
- **Phase 2 (Foundational)**: 13 tasks (BLOCKING)
- **Phase 3 (User Story 1 - P1)**: 18 tasks 🎯 MVP
- **Phase 4 (User Story 2 - P2)**: 19 tasks
- **Phase 5 (User Story 3 - P3)**: 13 tasks
- **Phase 6 (Polish)**: 10 tasks
- **Phase 7 (CI Integration)**: 12 tasks
- **Parallel Opportunities**: 50 tasks marked [P] (57% parallelizable)
- **MVP Scope**: Phases 1-3 (34 tasks) for basic shared storage functionality

---

## Constitution Compliance

✅ **Code Quality**: All tasks specify type hints, specific exceptions, and no hardcoded paths
✅ **Testing Discipline**: E2E tests in Phase 7 for all deployment modes (auto, all, web+worker)
✅ **UX Consistency**: Status messages and error guidance specified in tasks
✅ **Performance**: Target metrics embedded in task descriptions (<3min unit add, <2min upgrade)
