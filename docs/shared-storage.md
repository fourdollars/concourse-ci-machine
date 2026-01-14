# Shared Storage Architecture

This document details the architecture and data flow for the Concourse CI Shared Storage feature.

## Overview

The Shared Storage feature allows multiple Concourse units to share a single storage volume for binaries and keys, while maintaining isolated worker directories. This reduces disk usage and simplifies upgrades.

## Architecture

### Directory Structure

```
/var/lib/concourse/
├── bin/                        # Shared binaries (web/leader writes)
│   ├── concourse               # Main binary
│   └── gdn                     # Garden runc
├── .installed_version          # Version marker
├── .download_in_progress       # Progress indicator
├── .install.lock               # Lock file for coordination
├── keys/                       # Shared TSA keys
└── worker/                     # Per-unit state (workers write)
    ├── concourse-ci-0/
    │   ├── work_dir/
    │   └── state.json
    └── ...
```

## Data Flow Diagrams

### Initial Deployment Flow

```
┌─────────────────┐
│ Web/Leader Unit │
└────────┬────────┘
         │
         ├─ 1. Mount shared storage
         ├─ 2. Acquire exclusive lock
         ├─ 3. Download binaries to bin/
         ├─ 4. Write .installed_version
         └─ 5. Start concourse-server.service
         
┌──────────────┐
│ Worker Units │ (added later)
└──────┬───────┘
       │
       ├─ 1. Mount shared storage (same volume)
       ├─ 2. Check .installed_version (exists!)
       ├─ 3. Verify binaries in bin/
       ├─ 4. Create worker/{unit}/ directory
       └─ 5. Start concourse-worker.service
```

### Upgrade Flow

```
┌─────────────────┐
│ Web/Leader Unit │
└────────┬────────┘
         │
         ├─ 1. Set upgrade-state=prepare in peer relation
         ├─ 2. Wait for worker acknowledgments (2min timeout)
         ├─ 3. Acquire exclusive lock
         ├─ 4. Download new binaries
         ├─ 5. Write new .installed_version
         ├─ 6. Restart concourse-server.service
         └─ 7. Set upgrade-state=complete in peer relation
         
┌──────────────┐
│ Worker Units │
└──────┬───────┘
       │
       ├─ 1. Detect upgrade-state=prepare
       ├─ 2. Stop concourse-worker.service
       ├─ 3. Set upgrade-ready=true in peer relation
       ├─ 4. Poll for upgrade-state=complete (5min timeout)
       └─ 5. Start concourse-worker.service
```
