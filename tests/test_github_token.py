import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock ops unconditionally for unit testing ConcourseCharm
class DummyActiveStatus:
    pass
class DummyWaitingStatus:
    def __init__(self, message):
        self.message = message
class DummyBlockedStatus:
    def __init__(self, message):
        self.message = message
class DummyMaintenanceStatus:
    def __init__(self, message):
        self.message = message

ops_model = MagicMock()
ops_model.ActiveStatus = DummyActiveStatus
ops_model.WaitingStatus = DummyWaitingStatus
ops_model.BlockedStatus = DummyBlockedStatus
ops_model.MaintenanceStatus = DummyMaintenanceStatus
sys.modules["ops.model"] = ops_model

ops_charm = MagicMock()
ops_charm.CharmBase = type("CharmBase", (object,), {})
sys.modules["ops.charm"] = ops_charm

sys.modules["ops"] = MagicMock()
sys.modules["ops.main"] = MagicMock()

# Add lib/ and src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from concourse_installer import get_latest_concourse_version  # noqa: E402
from concourse_common import get_concourse_version  # noqa: E402


@patch("urllib.request.urlopen")
def test_get_latest_concourse_version_with_token(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"tag_name": "v7.14.3"}'
    mock_urlopen.return_value.__enter__.return_value = mock_response

    token = "test-github-token"
    version = get_latest_concourse_version(github_token=token)

    assert version == "7.14.3"
    
    # Verify that urlopen was called with a Request containing the correct Authorization header
    args, _ = mock_urlopen.call_args
    req = args[0]
    assert req.headers["Authorization"] == "Bearer test-github-token"


@patch("urllib.request.urlopen")
def test_get_latest_concourse_version_with_whitespace_token(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"tag_name": "v7.14.3"}'
    mock_urlopen.return_value.__enter__.return_value = mock_response

    token = "  test-github-token-with-whitespace  "
    version = get_latest_concourse_version(github_token=token)

    assert version == "7.14.3"
    
    # Verify that the token was stripped before generating the header
    args, _ = mock_urlopen.call_args
    req = args[0]
    assert req.headers["Authorization"] == "Bearer test-github-token-with-whitespace"


@patch("urllib.request.urlopen")
def test_get_latest_concourse_version_no_token(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"tag_name": "v7.14.3"}'
    mock_urlopen.return_value.__enter__.return_value = mock_response

    version = get_latest_concourse_version(github_token=None)

    assert version == "7.14.3"
    
    # Verify no Authorization header is present
    args, _ = mock_urlopen.call_args
    req = args[0]
    assert "Authorization" not in req.headers


@patch("urllib.request.urlopen")
def test_get_latest_concourse_version_empty_token(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"tag_name": "v7.14.3"}'
    mock_urlopen.return_value.__enter__.return_value = mock_response

    version = get_latest_concourse_version(github_token="")

    assert version == "7.14.3"
    
    # Verify no Authorization header is present
    args, _ = mock_urlopen.call_args
    req = args[0]
    assert "Authorization" not in req.headers


def test_get_concourse_version_propagates_token():
    config = {"version": "", "github-token": "test-github-token"}
    with patch("concourse_installer.get_latest_concourse_version") as mock_get_latest:
        mock_get_latest.return_value = "7.14.3"
        version = get_concourse_version(config)
        assert version == "7.14.3"
        mock_get_latest.assert_called_once_with(github_token="test-github-token")


def test_get_concourse_version_no_token():
    config = {"version": ""}
    with patch("concourse_installer.get_latest_concourse_version") as mock_get_latest:
        mock_get_latest.return_value = "7.14.3"
        version = get_concourse_version(config)
        assert version == "7.14.3"
        mock_get_latest.assert_called_once_with(github_token=None)


def test_get_concourse_version_empty_token():
    config = {"version": "", "github-token": ""}
    with patch("concourse_installer.get_latest_concourse_version") as mock_get_latest:
        mock_get_latest.return_value = "7.14.3"
        version = get_concourse_version(config)
        assert version == "7.14.3"
        mock_get_latest.assert_called_once_with(github_token="")


@patch("urllib.request.urlopen")
def test_concourse_helper_get_version_with_token(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"tag_name": "v7.14.3"}'
    mock_urlopen.return_value.__enter__.return_value = mock_response

    # Mock charm model and config
    charm = MagicMock()
    charm.model.config = {"version": "", "github-token": "test-helper-token"}
    
    from concourse_helper import ConcourseHelper
    helper = ConcourseHelper(charm)
    version = helper.get_concourse_version()
    
    assert version == "7.14.3"
    args, _ = mock_urlopen.call_args
    req = args[0]
    assert req.headers["Authorization"] == "Bearer test-helper-token"


@patch("urllib.request.urlopen")
def test_concourse_helper_get_version_no_token(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"tag_name": "v7.14.3"}'
    mock_urlopen.return_value.__enter__.return_value = mock_response

    # Mock charm model and config without github-token
    charm = MagicMock()
    charm.model.config = {"version": ""}
    
    from concourse_helper import ConcourseHelper
    helper = ConcourseHelper(charm)
    version = helper.get_concourse_version()
    
    assert version == "7.14.3"
    args, _ = mock_urlopen.call_args
    req = args[0]
    assert "Authorization" not in req.headers


def test_charm_on_update_status_version_with_token():
    from charm import ConcourseCharm

    charm = object.__new__(ConcourseCharm)
    charm.config = {"shared-storage": "lxc", "github-token": "test-github-token", "version": ""}
    charm.unit = MagicMock()
    charm.unit.status = DummyWaitingStatus("Waiting for shared storage mount")

    charm._should_run_web = MagicMock(return_value=True)
    charm._should_run_worker = MagicMock(return_value=False)
    charm.web_helper = MagicMock()

    # Mock Path.exists
    original_exists = Path.exists
    def mock_exists(path_obj):
        p = str(path_obj)
        if p == "/var/lib/concourse":
            return True
        if p == "/var/lib/concourse/.lxc_shared_storage":
            return True
        if p == "/var/lib/concourse/.installed_version":
            return False
        return original_exists(path_obj)

    with patch.object(Path, "exists", mock_exists):
        with patch("concourse_installer.get_latest_concourse_version") as mock_get_latest, \
             patch("concourse_installer.download_and_install_concourse_with_storage"):
            mock_get_latest.return_value = "7.14.3"
            
            charm._on_update_status(MagicMock())
            
            mock_get_latest.assert_called_once_with(github_token="test-github-token")


def test_charm_on_update_status_version_no_token():
    from charm import ConcourseCharm

    charm = object.__new__(ConcourseCharm)
    charm.config = {"shared-storage": "lxc", "version": ""}
    charm.unit = MagicMock()
    charm.unit.status = DummyWaitingStatus("Waiting for shared storage mount")

    charm._should_run_web = MagicMock(return_value=True)
    charm._should_run_worker = MagicMock(return_value=False)
    charm.web_helper = MagicMock()

    # Mock Path.exists
    original_exists = Path.exists
    def mock_exists(path_obj):
        p = str(path_obj)
        if p == "/var/lib/concourse":
            return True
        if p == "/var/lib/concourse/.lxc_shared_storage":
            return True
        if p == "/var/lib/concourse/.installed_version":
            return False
        return original_exists(path_obj)

    with patch.object(Path, "exists", mock_exists):
        with patch("concourse_installer.get_latest_concourse_version") as mock_get_latest, \
             patch("concourse_installer.download_and_install_concourse_with_storage"):
            mock_get_latest.return_value = "7.14.3"
            
            charm._on_update_status(MagicMock())
            
            mock_get_latest.assert_called_once_with(github_token=None)


def test_charm_on_update_status_version_empty_token():
    from charm import ConcourseCharm

    charm = object.__new__(ConcourseCharm)
    charm.config = {"shared-storage": "lxc", "github-token": "", "version": ""}
    charm.unit = MagicMock()
    charm.unit.status = DummyWaitingStatus("Waiting for shared storage mount")

    charm._should_run_web = MagicMock(return_value=True)
    charm._should_run_worker = MagicMock(return_value=False)
    charm.web_helper = MagicMock()

    # Mock Path.exists
    original_exists = Path.exists
    def mock_exists(path_obj):
        p = str(path_obj)
        if p == "/var/lib/concourse":
            return True
        if p == "/var/lib/concourse/.lxc_shared_storage":
            return True
        if p == "/var/lib/concourse/.installed_version":
            return False
        return original_exists(path_obj)

    with patch.object(Path, "exists", mock_exists):
        with patch("concourse_installer.get_latest_concourse_version") as mock_get_latest, \
             patch("concourse_installer.download_and_install_concourse_with_storage"):
            mock_get_latest.return_value = "7.14.3"
            
            charm._on_update_status(MagicMock())
            
            mock_get_latest.assert_called_once_with(github_token="")
