# SPDX-License-Identifier: GPL-2.0-only
"""Read-only host inventory. Identifiers remain private local runtime state."""
from __future__ import annotations

from pathlib import Path
import platform
import re
import shutil
import shlex

from .common import Error, run

PACKAGES = ('pve-manager', 'qemu-server', 'pve-qemu-kvm',
            'pve-edk2-firmware-ovmf')


def parse_config(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if line.startswith('['):
            break  # never parse snapshot sections as current configuration
        if not line or line.startswith('#') or ': ' not in line:
            continue
        key, value = line.split(': ', 1)
        if key in result:
            raise Error(f'Duplicate VM configuration key: {key}')
        result[key] = value
    return result


def read_optional(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def inventory() -> dict:
    if platform.system() != 'Linux' or not shutil.which('qm'):
        raise Error('Run detection on a Proxmox Linux host, or use --snapshot for offline planning.')
    packages = {p: run('dpkg-query', '-W', '-f=${Version}', p).strip()
                for p in PACKAGES}
    cpuinfo = read_optional('/proc/cpuinfo') or ''
    vendor = re.search(r'^vendor_id\s*:\s*(\S+)', cpuinfo, re.M)
    flags = re.search(r'^flags\s*:\s*(.+)', cpuinfo, re.M)
    model = re.search(r'^model name\s*:\s*(.+)', cpuinfo, re.M)
    dmi = {name: read_optional('/sys/class/dmi/id/' + name)
           for name in ('bios_vendor', 'bios_version', 'bios_date',
                        'board_vendor', 'board_name', 'board_version',
                        'sys_vendor', 'product_name', 'product_version')}
    guests = {}
    for config_file in sorted(Path('/etc/pve/qemu-server').glob('*.conf')):
        vmid = config_file.stem
        if not vmid.isdigit():
            continue
        config = parse_config(run('qm', 'config', vmid))
        guests[vmid] = {'status': run('qm', 'status', vmid).strip().split(': ', 1)[-1],
                        'config': config}
        generated = shlex.split(run('qm', 'showcmd', vmid, '--pretty'))
        cpus = [generated[i + 1] for i, arg in enumerate(generated[:-1]) if arg == '-cpu']
        devices = [generated[i + 1] for i, arg in enumerate(generated[:-1]) if arg == '-device']
        guests[vmid]['qemu_cpu'] = cpus[-1] if cpus else None
        guests[vmid]['has_xhci'] = any('id=xhci' in d or '"id":"xhci"' in d.replace(' ', '') for d in devices)
    containers = []
    if shutil.which('pct'):
        for line in run('pct', 'list').splitlines()[1:]:
            columns = line.split()
            if len(columns) >= 2 and columns[1] == 'running':
                containers.append(columns[0])
    kernel = platform.release()
    secure = run('mokutil', '--sb-state', check=False).strip() if shutil.which('mokutil') else 'unknown'
    return {
        'schema': 1, 'architecture': run('dpkg', '--print-architecture').strip(),
        'kernel': kernel, 'packages': packages,
        'cpu_vendor': vendor.group(1) if vendor else 'unknown',
        'cpu_model': model.group(1) if model else 'unknown',
        'vmx': bool(flags and 'vmx' in flags.group(1).split()),
        'dmi': dmi, 'secure_boot': secure,
        'lockdown': read_optional('/sys/kernel/security/lockdown'),
        'module_signature_enforcement': read_optional('/sys/module/module/parameters/sig_enforce'),
        'headers_present': Path('/usr/src/linux-headers-' + kernel + '/Module.symvers').is_file(),
        'kvm_path': run('modinfo', '-n', 'kvm').strip(),
        'kvm_parameter_present': Path('/sys/module/kvm/parameters/hidden_guest_ud').exists(),
        'package_held': 'pve-qemu-kvm' in run('apt-mark', 'showhold').splitlines(),
        'guests': guests, 'running_containers': containers,
        'ha_guests': re.findall(r'^vm:\s*(\d+)\s*$', read_optional('/etc/pve/ha/resources.cfg') or '', re.M),
    }


def redact(data: dict) -> dict:
    # Allowlist only. Do not publish UUIDs, names, serials, network addresses,
    # complete VM arguments, device paths or original vendor build suffixes.
    packages = {k: re.sub(r'\+.*$', '+local', v)
                for k, v in data['packages'].items()}
    return {k: data.get(k) for k in ('schema', 'architecture', 'kernel', 'cpu_vendor',
                                     'vmx', 'secure_boot', 'headers_present')} | {
        'packages': packages,
        'guest_count': len(data.get('guests', {})),
        'running_guest_count': sum(g['status'] == 'running' for g in data.get('guests', {}).values()),
        'running_container_count': len(data.get('running_containers', [])),
        'kvm_parameter_present': data.get('kvm_parameter_present', False),
        'ha_guest_count': len(data.get('ha_guests', [])),
    }
