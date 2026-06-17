import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Conditionally mock ops
try:
    import ops
except ImportError:
    sys.modules["ops"] = MagicMock()
    sys.modules["ops.model"] = MagicMock()

# Add lib/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import pytest
from concourse_installer import get_latest_concourse_version
from concourse_common import get_concourse_version


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
