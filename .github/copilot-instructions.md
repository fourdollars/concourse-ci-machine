# Copilot Instructions – concourse-ci-machine

A **Juju Machine Charm** for deploying [Concourse CI](https://concourse-ci.org/) on bare metal, VMs, or LXD containers (not Kubernetes). Requires Juju ≥ 3.1 and Ubuntu 24.04.

## Build, lint, and test

```bash
# Build
charmcraft pack                          # produces concourse-ci-machine_amd64.charm

# Lint / format / type-check
ruff check .
black .
mypy --ignore-missing-imports .

# Unit tests (all)
python -m pytest tests/ -v

# Single test file
python -m pytest tests/test_config.py -v

# Single test
python -m pytest tests/test_config.py::TestReadConfig::test_read_key_value_pairs -v
```

### Integration / deployment tests
```bash
# Full regression (build → deploy → verify → mounts → gpu → upgrade)
./scripts/deploy-test.sh

# Keep environment alive for debugging
./scripts/deploy-test.sh --skip-cleanup

# Jump to a specific step
./scripts/deploy-test.sh --goto=gpu --skip-cleanup

# Step-by-step (CI pattern)
./scripts/deploy-test.sh --mode=auto --skip-cleanup --steps=deploy
./scripts/deploy-test.sh --mode=auto --skip-cleanup --steps=verify
```

## Architecture

### Deployment modes (config `mode`)
| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Leader unit → Web node; all other units → Workers |
| `web` | Dedicated Web node (requires `postgresql` relation) |
| `worker` | Dedicated Worker node (requires `flight` relation to a `tsa` provider) |

`_get_deployment_mode()` in `src/charm.py` resolves the effective role (`"web"`, `"worker"`, `"both"`) at runtime.

### Juju relations
- **`postgresql`** (requires) – PostgreSQL 16/stable **only**. Uses `charms.data_platform_libs.v0.data_interfaces.DatabaseRequires`. A legacy path for older PostgreSQL charms also exists.
- **`tsa`** (provides) – Web node exposes TSA endpoint for workers.
- **`flight`** (requires) – Worker consumes TSA endpoint. Maps to the same handler as `tsa`.
- **`peers`** – Peer relation used to distribute SSH keys between units (zero manual setup required).
- **`monitoring`** (provides) – Prometheus scrape endpoint on port 9391 (enabled via `enable-metrics=true`).

### Key source files
| File | Responsibility |
|------|---------------|
| `src/charm.py` | Event wiring, lifecycle orchestration, mode selection |
| `lib/concourse_common.py` | Shared constants, user/dir creation, key generation, GPU detection |
| `lib/concourse_web.py` | Web systemd service, config generation (`web-config.env`), DB connection |
| `lib/concourse_worker.py` | Worker systemd service, containerd setup, GPU config, TSA connection, `worker-config.env` |
| `lib/concourse_installer.py` | Binary download and installation |
| `lib/folder_mount_manager.py` | Discovers `/srv/*` host mounts for injection into task containers |
| `lib/storage_coordinator.py` | Shared-storage coordination between units (LXC mode) |
| `src/folder_mount_manager.py` | Symlinked/duplicated mount manager used by the charm entrypoint |
| `hooks/runc-wrapper` | OCI runtime shim that injects host-folder bind mounts into container `config.json` |
| `hooks/runc-gpu-wrapper` | Like `runc-wrapper` but also injects NVIDIA GPU devices |

### Hook execution order
`install` → `config-changed` → `start` → `update-status` (periodic: 10 s in `test-mode`, 5 min normally).

`update-status` is the mechanism for detecting resources (e.g., shared storage) that become available after `install`.

### runc wrapper / folder mounting
1. `runc-wrapper` is installed to `/opt/bin/runc-wrapper`.
2. The real `runc` is backed up to `/opt/bin/runc.real`.
3. `/var/lib/concourse/bin/runc` becomes a symlink to the wrapper.
4. `worker-config.env` sets `PATH=/opt/bin:…` so the wrapper is resolved first.
5. The wrapper scans `/srv/*` on the host and injects those as bind mounts before calling the real `runc`.

### Shared storage (LXC mode, `shared-storage=lxc`)
- Units expect `/var/lib/concourse` to be an LXC-mounted disk device.
- Run `./scripts/setup-shared-storage.sh <app> <host-path>` **after** units are deployed (uses `shift=true` for UID/GID mapping).
- Workers added dynamically: `install` → waits → admin mounts → `update-status` auto-completes setup by calling `worker_helper.update_config()`.

## Key conventions

- **Idempotency is required.** Every hook may run multiple times; always guard file writes and installs with existence checks.
- **Config files are key=value env files** (`web-config.env`, `worker-config.env`). `ConcourseWebHelper._read_config` / `_write_config` perform a read-merge-sort-write cycle so existing values are preserved and the file stays deterministic.
- **Optional library imports with feature flags.** `data_platform_libs`, `prometheus_scrape`, `folder_mount_manager`, and `storage_coordinator` are all imported inside `try/except` blocks and guarded by `HAS_*` booleans. Follow this pattern for any new optional dependency.
- **Sensitive data → Juju Secrets.** Never log credentials. Use `self.unit.status` to surface errors (e.g., `BlockedStatus("Database missing")`).
- **Log levels:** `INFO` for notable events, `DEBUG` for diagnostics, `ERROR` for failures.
- **Type hints are mandatory** on all function signatures; docstrings required for all public modules, classes, and functions.
- **Always check `specs/` before starting complex tasks** – design specs live there.
- **PostgreSQL 16/stable only** – other versions are not supported.

## Debugging

```bash
juju debug-log --include concourse
juju debug-log --include concourse-worker/2 --replay --no-tail
juju ssh concourse/0 -- cat /var/log/concourse-ci.log
juju ssh concourse/0 -- journalctl -u concourse-server -n 50
juju ssh concourse/0 -- journalctl -u concourse-worker -n 50
```
