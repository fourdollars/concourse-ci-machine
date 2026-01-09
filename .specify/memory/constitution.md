<!--
=============================================================================
SYNC IMPACT REPORT
=============================================================================
Version Change: None → 1.0.0
Change Type: Initial constitution creation
Date: 2026-01-09

Principles Established:
  ✨ NEW: I. Code Quality Standards
  ✨ NEW: II. Testing Discipline  
  ✨ NEW: III. User Experience Consistency
  ✨ NEW: IV. Performance Requirements

Template Alignment Status:
  ✅ .specify/templates/plan-template.md - Constitution Check section aligned
  ✅ .specify/templates/spec-template.md - Success criteria aligned
  ✅ .specify/templates/tasks-template.md - Test task structure aligned
  ⚠️  No command templates found to update

Follow-up Actions:
  - Monitor charm deployment patterns against performance benchmarks
  - Establish baseline metrics for deployment times and resource usage
  - Review GPU worker performance standards in next iteration

Rationale for v1.0.0:
  Initial release establishing core development principles for the
  Concourse CI Machine charm project. Principles derive from production
  requirements for Juju charms, CI/CD reliability, and operational excellence.
=============================================================================
-->

# Concourse CI Machine Charm Constitution

## Core Principles

### I. Code Quality Standards

**Code MUST be production-ready, maintainable, and adhere to Juju best practices.**

- All Python code MUST follow PEP 8 style guidelines and pass linting (pylint, flake8, or ruff)
- Type hints MUST be used for all function signatures and class attributes
- Functions MUST be focused, single-purpose, with clear naming (no ambiguous verbs)
- Complex logic (>3 nested levels, >50 lines) MUST be refactored into smaller units
- Configuration handling MUST validate all inputs and provide clear error messages
- All secrets (passwords, keys, tokens) MUST use Juju secrets API - NEVER hardcode or log
- Charm code MUST handle relation lifecycle correctly (departed, broken, joined, changed)
- Error handling MUST be specific (catch exact exceptions, not bare except:)

**Rationale**: Juju charms are lifecycle managers - poor code quality leads to deployment failures, data loss, and security vulnerabilities in production environments.

### II. Testing Discipline

**Testing is mandatory for all charm features, with focus on integration and functional validation.**

- Integration tests MUST verify charm lifecycle events (install, config-changed, upgrade)
- Integration tests MUST validate relations (postgresql, peer, tsa) with real endpoints
- Unit tests MUST cover configuration validation, error handling, and state transitions
- All test failures MUST be investigated and resolved before merge - no ignored tests
- Tests MUST run in isolated environments (no shared state, clean setup/teardown)
- GPU features MUST include tests verifying nvidia-container-toolkit integration
- Database migrations and schema changes MUST include rollback tests
- Performance-critical paths (worker startup, key distribution) MUST have baseline benchmarks

**Critical Test Coverage**:
- Configuration changes trigger correct service restarts
- Peer relation key exchange works in multi-unit deployments
- PostgreSQL credential rotation doesn't break existing connections
- Upgrade action maintains worker connectivity during web server updates

**Rationale**: Charms deploy infrastructure - untested changes can cascade into cluster-wide failures. Integration tests catch real-world deployment issues that unit tests miss.

### III. User Experience Consistency

**Charm UX must be intuitive, predictable, and follow Juju conventions for operator ergonomics.**

- Configuration options MUST have clear descriptions and sensible defaults
- Status messages MUST be concise, actionable, and update in real-time during operations
- Blocked status MUST clearly state required action ("Waiting for PostgreSQL relation")
- Actions (upgrade, get-admin-password) MUST return structured output with clear messages
- Documentation MUST include quickstart examples that work without modification
- Error messages MUST guide users to resolution ("Run: juju relate web:postgresql postgresql:database")
- Port changes, version upgrades MUST apply dynamically without manual intervention
- All operational modes (auto, all, web, worker) MUST be documented with decision criteria

**UX Patterns**:
- Config validation happens early (config-changed) with specific error messages
- Progress indication for long operations (Concourse download, GPU toolkit installation)
- Automatic detection over manual config (external-url from IP, latest version from GitHub)
- Consistent naming: application "concourse-ci", charm "concourse-ci-machine"

**Rationale**: Poor UX creates operational friction - operators debug charm behavior instead of managing CI/CD. Clear status and actions reduce time-to-resolution.

### IV. Performance Requirements

**Charm operations must complete within reasonable timeframes to support rapid deployment and scaling.**

- Charm installation MUST complete within 10 minutes on typical hardware (4 CPU, 8GB RAM)
- Configuration changes (port, log-level) MUST apply within 30 seconds including service restart
- Worker scaling (add-unit) MUST complete within 5 minutes from empty to ready
- Peer key distribution MUST complete within 60 seconds for up to 10 units
- GPU worker initialization (nvidia-container-toolkit) MUST complete within 3 minutes
- Database queries (password retrieval, config read) MUST return within 2 seconds
- Status updates MUST reflect actual state within 10 seconds of change
- Resource downloads MUST show progress and handle network failures gracefully

**Performance Constraints**:
- Memory usage: Charm code <100MB resident, worker processes scale with workload
- Disk I/O: Minimize writes during steady state (no debug logging by default)
- Network: Batch peer relation updates, avoid chatty protocols
- CPU: Avoid blocking operations in event handlers (use subprocess for long tasks)

**Rationale**: Slow charms block entire deployment pipelines. Performance issues compound in multi-unit deployments where serial operations create bottlenecks.

## Operational Standards

### Deployment Reliability

- All systemd services MUST have restart policies and proper dependencies
- Services MUST be idempotent (repeated runs produce same result)
- Upgrade paths MUST be tested (version N to N+1, automatic worker upgrades)
- Failed upgrades MUST be recoverable without data loss
- Logs MUST be structured (timestamps, severity, component tags)
- Critical failures MUST set charm to blocked/error state with clear guidance

### Security Hardening

- TSA keys MUST be generated with secure entropy (ssh-keygen with proper key size)
- Admin password MUST be cryptographically random (32+ chars, mixed case/symbols)
- File permissions MUST follow least privilege (keys 0600, configs 0640)
- Container runtime MUST isolate workloads (no privileged containers unless explicitly configured)
- Secrets rotation MUST not cause service interruption (graceful credential updates)

### Documentation Standards

- README MUST include complete quick-start examples for each deployment mode
- Configuration options MUST document defaults, valid ranges, and side effects
- Architecture diagrams MUST reflect actual implementation (TSA flow, key exchange)
- Troubleshooting section MUST address common failure modes with solutions
- Breaking changes MUST be documented in release notes with migration steps

## Governance

### Amendment Process

1. Proposed changes MUST include rationale and impact analysis
2. Version bump follows semantic versioning:
   - **MAJOR**: Remove principle, change core requirement (e.g., drop Python 3.8 support)
   - **MINOR**: Add new principle, expand requirements (e.g., add observability mandate)
   - **PATCH**: Clarify wording, fix typos, add examples
3. All template files (.specify/templates/*.md) MUST be reviewed for alignment
4. Constitution updates MUST be committed separately from feature work

### Compliance Review

- All pull requests MUST reference this constitution in review checklist
- New features MUST document which principles they satisfy
- Complex features (cross-cutting concerns, breaking changes) MUST justify exceptions
- Quarterly review of principle effectiveness based on production incidents

### Living Document

This constitution evolves with project maturity. As deployment patterns emerge and production experience grows, principles are refined to reflect operational reality. Adherence is enforced through peer review, CI checks, and retrospectives on incidents.

**Version**: 1.0.0 | **Ratified**: 2025-12-17 | **Last Amended**: 2026-01-09
