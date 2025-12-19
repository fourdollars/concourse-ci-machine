# Changelog

All notable changes to the Concourse CI Machine Charm will be documented in this file.

## [Unreleased]

### Added
- **PostgreSQL 16+ Support**: Full integration with PostgreSQL 16 using `postgresql_client` interface
  - Automatic credential retrieval from Juju secrets API
  - Seamless connection string management
  - Compatible with PostgreSQL charm 16/stable
- **Dynamic Port Configuration**: Web port can be changed on-the-fly with automatic service restart
  - Configuration changes trigger immediate service restart
  - No manual intervention required
- **Privileged Port Support**: Run Concourse web on port 80 (or any port < 1024)
  - Uses `AmbientCapabilities=CAP_NET_BIND_SERVICE` in systemd service
  - No need for root user or manual capability configuration
- **Automatic External-URL Detection**: Automatically detects unit IP for external-url
  - Falls back to `http://<unit-ip>:<web-port>` if not configured
  - Can be overridden with `external-url` config option
- **Port Opening in Juju**: Automatically opens configured web port in Juju
  - Visible in `juju status` Ports column
  - Supports dynamic port changes
- **Ubuntu 24.04 LTS Support**: Optimized and tested for Ubuntu 24.04 LTS
  - Base set to `ubuntu@24.04`
  - Compatible with modern systemd features

### Changed
- **Base OS**: Migrated from Ubuntu 22.04 to Ubuntu 24.04 LTS
- **PostgreSQL Interface**: Changed from `pgsql` to `postgresql_client` interface
- **Relation Endpoint**: PostgreSQL relation now uses `database` endpoint instead of `db`
- **Service Restart**: Config changes now automatically restart services without manual intervention

### Technical Details
- Added `data_platform_libs` library for PostgreSQL 16+ integration
- Implemented Juju secrets API integration for secure credential management
- Enhanced systemd service configuration with Linux capabilities
- Improved configuration management with automatic service lifecycle handling

### Migration Notes
If upgrading from an older version:
1. PostgreSQL relation must use `database` endpoint: `juju integrate concourse-ci:postgresql postgresql:database`
2. Recommended to use PostgreSQL 16/stable: `juju deploy postgresql --channel 16/stable --base ubuntu@24.04`
3. Units must be deployed on Ubuntu 24.04 LTS

## [Earlier Versions]

See git history for changes in earlier versions.
