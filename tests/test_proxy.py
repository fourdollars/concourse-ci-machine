import os
import sys
from pathlib import Path

# Mock ops unconditionally for unit testing

# Add lib/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from concourse_installer import setup_juju_proxy  # noqa: E402


class TestSetupJujuProxy:
    def setup_method(self):
        self.original_env = os.environ.copy()

    def teardown_method(self):
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_proxy_variables_mapped_correctly(self):
        for key in [
            "http_proxy",
            "HTTP_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
        ]:
            os.environ.pop(key, None)

        os.environ["JUJU_CHARM_HTTP_PROXY"] = "http://127.0.0.1:18789"
        os.environ["JUJU_CHARM_HTTPS_PROXY"] = "http://127.0.0.1:18789"
        os.environ["JUJU_CHARM_NO_PROXY"] = "127.0.0.1,localhost"

        setup_juju_proxy()

        assert os.environ["http_proxy"] == "http://127.0.0.1:18789"
        assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:18789"
        assert os.environ["https_proxy"] == "http://127.0.0.1:18789"
        assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:18789"
        assert os.environ["no_proxy"] == "127.0.0.1,localhost"
        assert os.environ["NO_PROXY"] == "127.0.0.1,localhost"

    def test_preset_standard_proxy_is_not_overwritten(self):
        for key in [
            "http_proxy",
            "HTTP_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
        ]:
            os.environ.pop(key, None)

        os.environ["http_proxy"] = "http://already-set-proxy:3128"
        os.environ["HTTP_PROXY"] = "http://already-set-proxy:3128"

        os.environ["JUJU_CHARM_HTTP_PROXY"] = "http://juju-specific-proxy:18789"

        setup_juju_proxy()

        assert os.environ["http_proxy"] == "http://already-set-proxy:3128"
        assert os.environ["HTTP_PROXY"] == "http://already-set-proxy:3128"

    def test_setup_juju_proxy_no_ops_when_no_juju_env(self):
        for key in [
            "http_proxy",
            "HTTP_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
            "JUJU_CHARM_HTTP_PROXY",
            "JUJU_CHARM_HTTPS_PROXY",
            "JUJU_CHARM_NO_PROXY",
        ]:
            os.environ.pop(key, None)

        setup_juju_proxy()

        for key in [
            "http_proxy",
            "HTTP_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
        ]:
            assert key not in os.environ
