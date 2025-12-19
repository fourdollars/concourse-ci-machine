# Updates Summary - PostgreSQL 16 & Dynamic Port Configuration

Date: 2025-12-19

## Overview

This update adds full PostgreSQL 16+ support with Juju secrets integration and dynamic web port configuration including privileged port support.

## Modified Files

### Core Charm Files

1. **src/charm.py**
   - Added `DatabaseRequires` library integration for PostgreSQL 16+
   - Implemented Juju secrets API for credential retrieval
   - Added `_on_database_created` and `_on_database_changed` event handlers
   - Updated `_get_postgresql_url()` to support both old and new PostgreSQL interfaces
   - Added automatic port opening in Juju (`self.unit.open_port()`)
   - Enhanced configuration change handling with automatic service restart

2. **lib/concourse_web.py**
   - Added `AmbientCapabilities=CAP_NET_BIND_SERVICE` to systemd service
   - Enables binding to privileged ports (< 1024) without root

3. **lib/concourse_helper.py**
   - Updated systemd service template with capabilities support

### Configuration Files

4. **metadata.yaml**
   - Changed PostgreSQL interface from `pgsql` to `postgresql_client`
   - Updated description to highlight PostgreSQL 16+ and Ubuntu 24.04 support

5. **config.yaml**
   - Enhanced `web-port` description with dynamic change and privileged port info
   - Updated `external-url` description with auto-detection details

6. **charmcraft.yaml**
   - Changed base from Ubuntu 22.04 to Ubuntu 24.04
   - Updated to use `platforms` instead of `bases`

### Documentation Files

7. **README.md**
   - Updated features list with new capabilities
   - Changed all PostgreSQL references from 14/stable to 16/stable
   - Updated relation endpoint from `:db` to `:database`
   - Added Ubuntu 24.04 LTS as the supported base
   - Enhanced configuration examples with web-port dynamic changes
   - Updated deployment examples with `juju integrate` command
   - Added note about automatic credential management via Juju secrets

8. **CHANGELOG.md** (NEW)
   - Comprehensive changelog documenting all new features
   - Migration notes for existing deployments
   - Technical implementation details

9. **DEPLOYMENT_GUIDE.md** (NEW)
   - Quick reference guide for common deployment scenarios
   - Port forwarding setup instructions for LXD containers
   - Troubleshooting commands
   - Configuration examples

## Key Features Added

### 1. PostgreSQL 16+ Integration
- Uses `postgresql_client` interface
- Automatic credential retrieval from Juju secrets
- Seamless connection string management
- Compatible with modern PostgreSQL charm (16/stable)

### 2. Dynamic Port Configuration
- Web port can be changed on-the-fly
- Automatic service restart on configuration change
- No manual intervention required
- Supports any valid port number

### 3. Privileged Port Support
- Run on port 80 or any port < 1024
- Uses Linux capabilities (`CAP_NET_BIND_SERVICE`)
- No root user required
- Proper systemd service configuration

### 4. Automatic External-URL Detection
- Detects unit IP address automatically
- Constructs external-url as `http://<unit-ip>:<web-port>`
- Can be overridden with config option
- Ensures proper OAuth and webhook redirects

### 5. Port Management in Juju
- Automatically opens configured port using `juju expose`
- Visible in `juju status` Ports column
- Updates when web-port configuration changes

### 6. Ubuntu 24.04 LTS Support
- Optimized for Ubuntu 24.04
- Uses modern systemd features
- Tested with PostgreSQL 16 on Ubuntu 24.04

## Testing Performed

### Successful Tests on Pico Environment
✅ PostgreSQL 16 integration with Juju secrets
✅ Web port dynamic changes (80 → 8888 → 80)
✅ Privileged port 80 operation
✅ External-URL automatic detection
✅ Port opening in Juju status
✅ Web UI access and authentication
✅ Service restart on configuration change
✅ Ubuntu 24.04 LXD container deployment

### Verified Deployment
- **Environment**: Pico host with LXD
- **PostgreSQL**: 16/stable on Ubuntu 24.04
- **Concourse CI**: Latest version on Ubuntu 24.04
- **Access**: http://192.168.50.130 (external) and http://10.118.245.78 (internal)
- **Credentials**: Admin password automatically generated and retrievable via action

## Migration Guide

For existing deployments upgrading to this version:

1. **PostgreSQL Relation Change**
   ```bash
   # Remove old relation
   juju remove-relation concourse-ci postgresql
   
   # Add new relation with correct endpoint
   juju integrate concourse-ci:postgresql postgresql:database
   ```

2. **PostgreSQL Version**
   - Recommended to use PostgreSQL 16/stable
   - Deploy with: `juju deploy postgresql --channel 16/stable --base ubuntu@24.04`

3. **Base OS**
   - New deployments should use Ubuntu 24.04
   - Units will need to be redeployed on Ubuntu 24.04 for full support

## Breaking Changes

- PostgreSQL relation endpoint changed from `db` to `database`
- Base OS changed to Ubuntu 24.04 (Ubuntu 22.04 no longer supported)
- PostgreSQL interface changed to `postgresql_client`

## Files Not Modified

The following files were intentionally not modified:
- GPU-related files (still reference Ubuntu 22.04 CUDA images - this is correct)
- Test files and scripts
- Hook scripts (auto-generated by Juju)
- License and project metadata

## Verification Commands

```bash
# Check current status
juju status

# Verify port is open
juju ssh concourse-ci/0 'sudo ss -tlnp | grep concourse'

# Get admin credentials
juju run concourse-ci/leader get-admin-password

# Test port change
juju config concourse-ci web-port=8888
# Wait 30 seconds
juju ssh concourse-ci/0 'sudo ss -tlnp | grep 8888'

# Test web access
curl -I http://<unit-ip>:<port>/
```

## Next Steps

1. Test the charm in various deployment scenarios
2. Consider publishing to Charmhub
3. Update any CI/CD pipelines to use Ubuntu 24.04
4. Document any environment-specific network configuration (NAT, firewalls)

## Credits

Implementation completed on 2025-12-19 with full testing on Pico environment.
