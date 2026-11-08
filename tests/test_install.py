"""The root helper, its sudoers entry and the unit ship with the repo and match the config defaults."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from wlanpi_menud.config import Config

INSTALL = Path(__file__).resolve().parent.parent / "install"
HELPER = INSTALL / "usr/libexec/wlanpi-menud/get-core-token"


def test_helper_is_executable_and_takes_no_arguments():
    assert HELPER.stat().st_mode & stat.S_IXUSR
    r = subprocess.run([str(HELPER), "--port", "1"], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr
    assert "getjwt wlanpi-menud" in HELPER.read_text()


def test_sudoers_only_wlanpi_only_the_helper_no_args():
    text = (INSTALL / "etc/sudoers.d/wlanpi-menud").read_text()
    rules = [ln for ln in text.splitlines() if ln and not ln.startswith(("#", "Defaults"))]
    assert rules == ['wlanpi ALL=(root) NOPASSWD: /usr/libexec/wlanpi-menud/get-core-token ""']


def test_config_defaults_match_install_layout():
    c = Config()
    assert c.token_command == ("sudo", "-n", "/usr/libexec/wlanpi-menud/get-core-token")
    assert str(c.local_sock) == "/run/wlanpi-menud/local.sock" and str(c.proxy_sock) == "/run/wlanpi-menud/proxy.sock"
    assert c.proxy_sock_mode == 0o660 and c.core_base_url == "https://127.0.0.1:31415"
    unit = (INSTALL / "lib/systemd/system/wlanpi-menud.service").read_text()
    assert "User=wlanpi" in unit and "RuntimeDirectory=wlanpi-menud" in unit
