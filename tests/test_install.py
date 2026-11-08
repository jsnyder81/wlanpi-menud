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


def test_protocol_dir_env_override_and_shared_fallback(tmp_path, monkeypatch):
    from wlanpi_menud import manifest

    (tmp_path / "menu.schema.json").write_text("{}")
    monkeypatch.setenv("WLANPI_MENUD_PROTOCOL_DIR", str(tmp_path))
    assert manifest._find_protocol_dir() == tmp_path
    monkeypatch.delenv("WLANPI_MENUD_PROTOCOL_DIR")
    assert (manifest._find_protocol_dir() / "menu.schema.json").is_file()  # the checkout copy
    assert str(manifest.SHARED_PROTOCOL_DIR) == "/usr/share/wlanpi-menud/protocol"


def test_debian_package_installs_what_the_defaults_point_at():
    root = INSTALL.parent
    dest = {}
    for line in (root / "debian/wlanpi-menud.install").read_text().splitlines():
        src, target = line.split()
        dest[src] = target
    assert dest["install/usr/libexec/wlanpi-menud/get-core-token"] == "usr/libexec/wlanpi-menud/"
    assert dest["install/etc/sudoers.d/wlanpi-menud"] == "etc/sudoers.d/"
    assert dest["protocol/menu.schema.json"] == "usr/share/wlanpi-menud/protocol/"
    assert dest["share/menu.d/*.json"] == "usr/share/wlanpi-menud/menu.d/"
    rules = (root / "debian/rules").read_text()
    assert "chmod 0440 debian/$(PACKAGE)/etc/sudoers.d/wlanpi-menud" in rules
    assert '--install-suffix "wlanpi-menud"' in rules and "DH_VIRTUALENV_INSTALL_ROOT=/opt" in rules
    unit = (INSTALL / "lib/systemd/system/wlanpi-menud.service").read_text()
    assert "NoNewPrivileges=yes" not in unit  # sudo (setuid) must keep working
    assert "SupplementaryGroups=www-data" in unit and "RuntimeDirectoryMode=0755" in unit
