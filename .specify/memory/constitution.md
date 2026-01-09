<!--
=============================================================================
SYNC IMPACT REPORT
=============================================================================
Version Change: 1.0.0 → 1.0.1
Change Type: PATCH - Clarifications based on GitHub Actions validation
Date: 2026-01-09

Principles Modified:
  📝 CLARIFIED: I. Code Quality Standards
     - Acknowledged linting/type hints as aspirational (no CI enforcement yet)
     - Retained MUST language to establish target state
  
  📝 CLARIFIED: II. Testing Discipline
     - Emphasized E2E deployment tests as PRIMARY validation
     - Clarified integration tests focus on deployment modes/upgrades
     - Unit tests noted as future enhancement
  
  📝 REFINED: IV. Performance Requirements
     - Updated "installation" → "deployment settling" (more accurate)
     - Aligned timeframes with measured CI results (<5min settling)
     - Added CI test suite completion benchmark (<30min)

GitHub Actions Validation Results:
  ✅ E2E tests cover 3 deployment modes (all, auto, web+worker)
  ✅ Upgrade testing validates 7.14.2 → 7.14.3 in all modes
  ✅ Measured deployment settling: 2:48 - 4:06 minutes
  ✅ CI suite completion: ~17 minutes (well under 30min target)
  ⚠️  No linting job in CI (aspirational requirement)
  ⚠️  No unit tests present (E2E deployment tests are primary)

Template Alignment Status:
  ✅ .specify/templates/plan-template.md - No changes needed
  ✅ .specify/templates/spec-template.md - No changes needed
  ✅ .specify/templates/tasks-template.md - No changes needed

Follow-up Actions:
  - Consider adding lint job to CI (pylint/flake8/ruff)
  - Consider adding type checking (mypy) as CI gate
  - Monitor CI duration to ensure <30min target maintained
  - Establish unit test framework as project matures

Rationale for v1.0.1 (PATCH):
  GitHub Actions review revealed terminology misalignment and aspirational
  requirements not yet enforced. Clarifications ensure constitution reflects
  actual practice while retaining aspirational targets. No semantic changes
  to principles, only wording refinements and measured validation.
=============================================================================
-->

# Concourse CI Machine Charm Constitution

## Core Principles

### I. Code Quality Standards

**Code MUST be production-ready, maintainable, and adhere to Juju best practices.**

- All Python code MUST follow PEP 8 style guidelines and pass linting (pylint, flake8, or ruff)*
- Type hints MUST be used for all function signatures and class attributes*
- Functions MUST be focused, single-purpose, with clear naming (no ambiguous verbs)
- Complex logic (>3 nested levels, >50 lines) MUST be refactored into smaller units
- Configuration handling MUST validate all inputs and provide clear error messages
- All secrets (passwords, keys, tokens) MUST use Juju secrets API - NEVER hardcode or log
- Charm code MUST handle relation lifecycle correctly (departed, broken, joined, changed)
- Error handling MUST be specific (catch exact exceptions, not bare except:)

_* Linting and type checking are currently aspirational targets. CI enforcement to be added in future iterations._

**Rationale**: Juju charms are lifecycle managers - poor code quality leads to deployment failures, data loss, and security vulnerabilities in production environments. While automated enforcement is still being established, these standards guide code review and manual verification.

### II. Testing Discipline

**E2E deployment testing is mandatory, with focus on real-world integration validation.**

- **Primary: E2E Deployment Tests** - CI MUST validate all deployment modes (all, auto, web+worker)
- E2E tests MUST verify complete deployment lifecycle with real Juju, PostgreSQL, and Concourse
- Upgrade paths MUST be tested in CI (version N → N+1 for all deployment modes)
- Integration tests MUST validate relations (postgresql, peer, tsa) with actual endpoints
- Worker registration, mount functionality, and tag targeting MUST be verified end-to-end
- Unit tests for configuration validation and error handling are encouraged but not yet mandatory
- All test failures MUST be investigated and resolved before merge - no ignored tests
- Tests MUST run in isolated environments (no shared state, clean setup/teardown)
- GPU features MUST include tests verifying nvidia-container-toolkit integration
- CI test suite MUST complete within 30 minutes to support rapid iteration

**Critical Test Coverage**:
- Configuration changes trigger correct service restarts
- Peer relation key exchange works in multi-unit deployments
- PostgreSQL credential rotation doesn't break existing connections
- Upgrade action maintains worker connectivity during web server updates
- Version changes propagate correctly across related applications

**Rationale**: Charms deploy infrastructure - E2E deployment tests catch real-world failures that unit tests miss. Current testing strategy prioritizes comprehensive deployment validation over isolated unit testing, reflecting the integration-heavy nature of charm development.

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

- **Deployment settling** MUST complete within 5 minutes (from deploy to active/ready state)
- Configuration changes (port, log-level) MUST apply within 30 seconds including service restart
- Upgrade operations MUST complete within 2 minutes including version propagation
- Worker scaling (add-unit) MUST reach ready state within 5 minutes from empty
- Peer key distribution MUST complete within 60 seconds for up to 10 units
- GPU worker initialization (nvidia-container-toolkit) MUST complete within 3 minutes
- Database queries (password retrieval, config read) MUST return within 2 seconds
- Status updates MUST reflect actual state within 10 seconds of change
- Resource downloads MUST show progress and handle network failures gracefully (retry with backoff)
- **CI test suite** MUST complete within 30 minutes for all deployment modes

**Measured Baselines** (from CI validation):
- mode=all deployment settling: ~2:48 minutes ✅
- mode=auto (2 units) settling: ~3:32 minutes ✅
- mode=web+worker settling: ~4:06 minutes ✅
- Upgrade (7.14.2 → 7.14.3): ~1:20 minutes ✅
- Full CI suite (build + 3 test modes): ~17 minutes ✅

**Performance Constraints**:
- Memory usage: Charm code <100MB resident, worker processes scale with workload
- Disk I/O: Minimize writes during steady state (no debug logging by default)
- Network: Batch peer relation updates, avoid chatty protocols
- CPU: Avoid blocking operations in event handlers (use subprocess for long tasks)

**Rationale**: Slow charms block entire deployment pipelines. Performance issues compound in multi-unit deployments where serial operations create bottlenecks. Measured baselines from CI provide concrete targets and validation that requirements are achievable.

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

**Version**: 1.0.1 | **Ratified**: 2025-12-17 | **Last Amended**: 2026-01-09
