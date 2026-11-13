"""The root helper, its sudoers entry and the unit ship with the repo and match the config defaults."""

from __future__ import annotations

import stat
import subprocess
import time
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
    assert str(c.local_sock) == "/run/wlanpi-menud/local.sock" and str(c.proxy_sock) == "/run/wlanpi-menud/proxy/proxy.sock"
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
    assert not any(ln.startswith("SupplementaryGroups") for ln in unit.splitlines()) and "RuntimeDirectoryMode=0750" in unit
    assert "Type=notify" in unit and "CPUWeight=50" in unit
    assert "MemoryHigh=80M" in unit and "MemoryMax=112M" in unit
    assert "ExecStartPre=+/usr/bin/systemd-tmpfiles --create wlanpi-menud.conf" in unit
    tmpfiles = (root / "debian/wlanpi-menud.tmpfiles").read_text()
    entries = [ln.split() for ln in tmpfiles.splitlines() if ln and not ln.startswith("#")]
    assert ["d", "/run/wlanpi-menud", "0750", "wlanpi", "wlanpi-menud", "-"] in entries
    assert ["a+", "/run/wlanpi-menud", "-", "-", "-", "-", "group:www-data:--x"] in entries
    assert ["d", "/run/wlanpi-menud/proxy", "2750", "wlanpi", "www-data", "-"] in entries
    assert str(Config().proxy_sock.parent) == "/run/wlanpi-menud/proxy"


def _run_postrm(tmp_path, group_line: str, passwd: str, action: str = "purge"):
    """Run debian/wlanpi-menud.postrm with getent/groupdel/rm stubbed on PATH."""
    bin_ = tmp_path / f"bin-{time.time_ns()}"
    bin_.mkdir()
    log_grp = tmp_path / f"log-grp-{time.time_ns()}"
    log_rm = tmp_path / f"log-rm-{time.time_ns()}"
    (bin_ / "getent").write_text(f"""#!/bin/sh
if [ "$1" = group ]; then [ -n "{group_line}" ] && echo "{group_line}" || exit 2; fi
if [ "$1" = passwd ]; then printf '%s' "{passwd}"; fi
""")
    (bin_ / "groupdel").write_text(f'#!/bin/sh\necho "groupdel $*" >> {log_grp}\n')
    (bin_ / "rm").write_text(f'#!/bin/sh\necho "rm $*" >> {log_rm}\n')
    (bin_ / "rmdir").write_text("#!/bin/sh\nexit 0\n")  # never touch the host
    for f in bin_.iterdir():
        f.chmod(0o755)
    script = (INSTALL.parent / "debian/wlanpi-menud.postrm").read_text().replace("#DEBHELPER#", "")
    r = subprocess.run(["sh", "-c", script, "postrm", action], capture_output=True, text=True, check=False,
                       env={"PATH": f"{bin_}:/usr/bin:/bin"})
    return r, (log_grp.read_text() if log_grp.exists() else ""), (log_rm.read_text() if log_rm.exists() else "")


def test_postrm_purge_removes_unused_group(tmp_path):
    r, grp_log, _ = _run_postrm(tmp_path, "wlanpi-menud:x:986:", "wlanpi:x:1000:1000::/home/wlanpi:/bin/sh\n")
    assert r.returncode == 0 and grp_log == "groupdel wlanpi-menud\n"


def test_postrm_purge_keeps_group_with_members(tmp_path):
    r, grp_log, _ = _run_postrm(tmp_path, "wlanpi-menud:x:986:alice", "wlanpi:x:1000:1000::/home/wlanpi:/bin/sh\n")
    assert r.returncode == 0 and grp_log == "" and "keeping group" in r.stderr


def test_postrm_purge_keeps_group_that_is_a_primary_group(tmp_path):
    r, grp_log, _ = _run_postrm(tmp_path, "wlanpi-menud:x:986:", "svc:x:990:986::/:/usr/sbin/nologin\n")
    assert r.returncode == 0 and grp_log == ""


def test_postrm_remove_unlinks_webui_snippet(tmp_path):
    r, grp_log, rm_log = _run_postrm(tmp_path, "wlanpi-menud:x:986:", "", action="remove")
    assert r.returncode == 0
    assert grp_log == ""
    assert "rm -f /etc/wlanpi-webui/nginx/conf.d/wlanpi-menud.conf" in rm_log
    r, grp_log, _ = _run_postrm(tmp_path, "", "")
    assert r.returncode == 0 and grp_log == ""


def test_complications_ship_in_the_package():
    root = INSTALL.parent
    dest = dict(line.split() for line in (root / "debian/wlanpi-menud.install").read_text().splitlines())
    assert dest["protocol/complications.schema.json"] == "usr/share/wlanpi-menud/protocol/"
    assert dest["share/complications.d/defaults.json"] == "usr/share/wlanpi-menud/complications.d/"
    links = (root / "debian/wlanpi-menud.links").read_text().splitlines()
    assert "opt/wlanpi-menud/bin/wlanpi-complication usr/bin/wlanpi-complication" in links
    assert 'wlanpi-complication = "wlanpi_menud.cli:main"' in (root / "pyproject.toml").read_text()
    unit = (INSTALL / "lib/systemd/system/wlanpi-menud.service").read_text()
    assert "StateDirectory=wlanpi-menud" in unit
    c = Config()
    assert str(c.complications_user_file) == "/var/lib/wlanpi-menud/complications.json"
    assert str(c.complications_site_file) == "/etc/wlanpi-menud/complications.json"
    assert str(c.complications_defaults_file) == "/usr/share/wlanpi-menud/complications.d/defaults.json"


def test_shipped_defaults_match_the_code():
    import json

    from wlanpi_menud.complications import DEFAULT_SHOW

    doc = json.loads((INSTALL.parent / "share/complications.d/defaults.json").read_text())
    assert {k: v["show"] for k, v in doc["surfaces"].items()} == DEFAULT_SHOW
