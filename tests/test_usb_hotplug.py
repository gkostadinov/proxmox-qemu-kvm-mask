"""Failure-path tests for the optional Proxmox USB hotplug helper.

The production scripts use fixed Proxmox paths. These tests substitute only those
paths in temporary copies and provide synthetic Perl modules / shell commands;
they never contact a Proxmox host or edit system configuration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "pve-usb-hotplug"
INSTALLER = ROOT / "tools" / "install-pve-usb-hotplug.sh"
UNIT = ROOT / "systemd" / "pve-usb-hotplug@.service"
VMID = "200"


def _write(path: Path, contents: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _replace_proxmox_paths(source: str, root: Path) -> str:
    substitutions = {
        "/etc/pve/qemu-server": str(root / "pve-config"),
        "/run/qemu-server": str(root / "run"),
        "/var/lib/pve-usb-hotplug": str(root / "state"),
        "/usr/local/libexec": str(root / "installed-helper"),
        "/etc/systemd/system": str(root / "installed-unit"),
    }
    for original, temporary in substitutions.items():
        source = source.replace(original, temporary)
    return source


@unittest.skipUnless(os.name == "posix", "requires a POSIX shell and Perl")
class UsbHotplugFailureTests(unittest.TestCase):
    def test_failed_live_selector_check_removes_new_device_and_keeps_pending(self) -> None:
        """A QMP add that cannot be verified must not leave an orphaned usb-host."""
        if not shutil.which("perl"):
            self.skipTest("Perl is unavailable")

        with tempfile.TemporaryDirectory(prefix="usb-hotplug-test-") as temporary:
            root = Path(temporary)
            config = root / "pve-config" / f"{VMID}.conf"
            _write(
                config,
                "ostype: other\n"
                "smbios1: uuid=00000000-0000-4000-8000-000000000200\n"
                "hotplug: usb\n"
                "[PENDING]\n"
                "usb0: host=1-1,usb3=1\n",
            )
            _write(root / "run" / f"{VMID}.pid", "12345\n")
            helper = root / "pve-usb-hotplug"
            _write(helper, _replace_proxmox_paths(HELPER.read_text(encoding="utf-8"), root))

            modules = {
                "PVE/Cluster.pm": "package PVE::Cluster; sub cfs_update { 1 } 1;\n",
                "PVE/QemuConfig.pm": r'''
package PVE::QemuConfig;
sub lock_config_full { my ($class, $vmid, $seconds, $callback) = @_; $callback->(); }
sub load_config {
    return {
        ostype => 'other',
        smbios1 => 'uuid=00000000-0000-4000-8000-000000000200',
        hotplug => 'usb',
        pending => { usb0 => 'host=1-1,usb3=1' },
    };
}
sub check_lock { 1 }
sub parse_pending_delete { return {}; }
sub write_config {
    open(my $out, '>>', $ENV{TEST_QMP_LOG}) or die $!;
    print $out "write_config\n";
    close($out);
}
1;
''',
                "PVE/QemuServer.pm": r'''
package PVE::QemuServer;
sub parse_hotplug_features { return { usb => 1 }; }
sub qemu_devicedelverify { 1 }
1;
''',
                "PVE/QemuServer/Machine.pm": r'''
package PVE::QemuServer::Machine;
sub get_current_qemu_machine { return 'pc-q35-11.0'; }
sub is_machine_version_at_least { return 1; }
1;
''',
                "PVE/QemuServer/USB.pm": r'''
package PVE::QemuServer::USB;
sub parse_usb_device { return { hostbus => 1, hostport => '1' }; }
1;
''',
                "PVE/JSONSchema.pm": r'''
package PVE::JSONSchema;
sub parse_property_string { return { host => '1-1', usb3 => 1 }; }
sub print_property_string { return 'host=1-1,usb3=1'; }
1;
''',
                "PVE/QemuServer/Monitor.pm": r'''
package PVE::QemuServer::Monitor;
use Exporter 'import';
our @EXPORT_OK = qw(mon_cmd);
my $present = 0;
sub log_call {
    my ($name) = @_;
    open(my $out, '>>', $ENV{TEST_QMP_LOG}) or die $!;
    print $out "$name\n";
    close($out);
}
sub mon_cmd {
    my ($vmid, $command, %args) = @_;
    return { status => 'running' } if $command eq 'query-status';
    if ($command eq 'qom-list') {
        my @items = ({ name => 'xhci', type => 'child<qemu-xhci>' });
        push @items, { name => 'usb0', type => 'child<usb-host>' } if $present;
        return \@items;
    }
    if ($command eq 'qom-get') {
        return 7 if $args{path} eq '/machine/peripheral/xhci'
            && ($args{property} eq 'p2' || $args{property} eq 'p3');
        if ($args{path} eq '/machine/peripheral/usb0') {
            # Simulate a QMP success whose resulting selector is not the requested host port.
            return 2 if $args{property} eq 'hostbus';
            return '2' if $args{property} eq 'hostport';
            return 0 if $args{property} eq 'vendorid' || $args{property} eq 'productid';
            return '/machine/peripheral/xhci.0' if $args{property} eq 'parent_bus';
            return '1' if $args{property} eq 'port';
        }
    }
    if ($command eq 'device_add') { $present = 1; log_call('device_add'); return {}; }
    if ($command eq 'device_del') { $present = 0; log_call('device_del'); return {}; }
    die "Unexpected QMP command $command\n";
}
1;
''',
            }
            for name, source in modules.items():
                _write(root / "perl-lib" / name, source)

            log = root / "qmp.log"
            env = os.environ.copy()
            env["PERL5LIB"] = str(root / "perl-lib")
            env["TEST_QMP_LOG"] = str(log)
            result = subprocess.run(
                ["perl", str(helper), "--vmid", VMID, "--once"],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("live selector verification failed", result.stdout + result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["device_add", "device_del"])
            self.assertIn("usb0: host=1-1,usb3=1", config.read_text(encoding="utf-8"))

    def test_installer_preflight_failure_does_not_install_or_restart(self) -> None:
        """A rejected VM leaves both the installed helper and services untouched."""
        with tempfile.TemporaryDirectory(prefix="usb-hotplug-install-test-") as temporary:
            root = Path(temporary)
            _write(root / "pve-config" / f"{VMID}.conf", "ostype: other\n")
            installer = root / "tools" / "install-pve-usb-hotplug.sh"
            _write(installer, _replace_proxmox_paths(INSTALLER.read_text(encoding="utf-8"), root), True)
            _write(root / "tools" / "pve-usb-hotplug", HELPER.read_text(encoding="utf-8"))
            _write(root / "systemd" / "pve-usb-hotplug@.service", UNIT.read_text(encoding="utf-8"))

            mock_bin = root / "mock-bin"
            _write(mock_bin / "id", "#!/bin/sh\nprintf '0\\n'\n", True)
            _write(
                mock_bin / "perl",
                "#!/bin/sh\nprintf 'perl %s\\n' \"$*\" >> \"$TEST_ACTION_LOG\"\n"
                "case \"$*\" in *--check*) exit 23 ;; esac\nexit 0\n",
                True,
            )
            for command in ("install", "systemd-analyze", "systemctl"):
                _write(
                    mock_bin / command,
                    f"#!/bin/sh\nprintf '{command} %s\\n' \"$*\" >> \"$TEST_ACTION_LOG\"\nexit 0\n",
                    True,
                )

            log = root / "actions.log"
            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
            env["TEST_ACTION_LOG"] = str(log)
            result = subprocess.run(
                ["/bin/sh", str(installer), VMID],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
            actions = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(actions), 2, actions)
            self.assertIn("-c", actions[0])
            self.assertIn("--check", actions[1])
            self.assertFalse(any(action.startswith(("install", "systemd", "systemctl")) for action in actions))

    def test_installer_restart_failure_restores_previous_files_and_instance(self) -> None:
        """A failed restart restores installed files and retries the old instance."""
        if not Path("/usr/bin/install").is_file():
            self.skipTest("GNU install is unavailable")

        with tempfile.TemporaryDirectory(prefix="usb-hotplug-rollback-test-") as temporary:
            root = Path(temporary)
            _write(root / "pve-config" / f"{VMID}.conf", "ostype: other\n")
            installer = root / "tools" / "install-pve-usb-hotplug.sh"
            _write(installer, _replace_proxmox_paths(INSTALLER.read_text(encoding="utf-8"), root), True)
            _write(root / "tools" / "pve-usb-hotplug", HELPER.read_text(encoding="utf-8"))
            _write(root / "systemd" / "pve-usb-hotplug@.service", UNIT.read_text(encoding="utf-8"))

            prior_helper = root / "installed-helper" / "pve-usb-hotplug"
            prior_unit = root / "installed-unit" / "pve-usb-hotplug@.service"
            _write(prior_helper, "previous helper\n", True)
            _write(prior_unit, "previous unit\n")

            mock_bin = root / "mock-bin"
            _write(mock_bin / "id", "#!/bin/sh\nprintf '0\\n'\n", True)
            _write(mock_bin / "perl", "#!/bin/sh\nexit 0\n", True)
            _write(
                mock_bin / "install",
                "#!/bin/sh\nprintf 'install %s\\n' \"$*\" >> \"$TEST_ACTION_LOG\"\n"
                "exec /usr/bin/install \"$@\"\n",
                True,
            )
            _write(
                mock_bin / "systemd-analyze",
                "#!/bin/sh\nprintf 'verify %s\\n' \"$*\" >> \"$TEST_ACTION_LOG\"\nexit 0\n",
                True,
            )
            _write(
                mock_bin / "systemctl",
                "#!/bin/sh\nprintf 'systemctl %s\\n' \"$*\" >> \"$TEST_ACTION_LOG\"\n"
                "case \"$1\" in\n"
                "  is-active|is-enabled) exit 0 ;;\n"
                "  restart)\n"
                "    if [ ! -e \"$TEST_RESTART_ONCE\" ]; then\n"
                "      : > \"$TEST_RESTART_ONCE\"\n"
                "      exit 71\n"
                "    fi ;;\n"
                "esac\nexit 0\n",
                True,
            )

            (root / "tmp").mkdir()
            log = root / "actions.log"
            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
            env["TEST_ACTION_LOG"] = str(log)
            env["TEST_RESTART_ONCE"] = str(root / "restart-once")
            env["TMPDIR"] = str(root / "tmp")
            result = subprocess.run(
                ["/bin/sh", str(installer), VMID],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 71, result.stdout + result.stderr)
            self.assertIn("previous helper, unit, and instance state restored", result.stderr)
            self.assertEqual(prior_helper.read_text(encoding="utf-8"), "previous helper\n")
            self.assertEqual(prior_unit.read_text(encoding="utf-8"), "previous unit\n")
            actions = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum("systemctl restart" in action for action in actions), 2, actions)
            self.assertEqual(sum("systemctl daemon-reload" in action for action in actions), 2, actions)
            self.assertEqual(list((root / "tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
