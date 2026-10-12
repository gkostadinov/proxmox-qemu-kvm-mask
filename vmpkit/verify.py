# SPDX-License-Identifier: GPL-2.0-only
"""Boot disposable Linux guests; never attach production disks or networking."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .build import check_artifacts
from .common import Error, digest, run

PROBE = r'''
#include <cpuid.h>
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
static sigjmp_buf jump;
static volatile sig_atomic_t caught;
static void fault(int sig) { caught = sig; siglongjmp(jump, 1); }
static int call(int which) {
  caught = 0;
  if (!sigsetjmp(jump, 1)) {
    if (which) __asm__ volatile(".byte 0x0f,0x01,0xd9" ::: "memory", "rax");
    else __asm__ volatile(".byte 0x0f,0x01,0xc1" ::: "memory", "rax");
  }
  return caught;
}
int main(void) {
  unsigned a,b,c,d;
  __get_cpuid(1,&a,&b,&c,&d);
  signal(SIGILL,fault); signal(SIGSEGV,fault);
  int v=call(0), m=call(1);
  printf("VMP_PROBE:{\"hypervisor\":%u,\"vmcall_signal\":%d,\"vmmcall_signal\":%d}\n",c>>31,v,m);
  return 0;
}
'''


def write_cpio(root: Path, target: Path) -> None:
    # newc archive without a host cpio dependency. No host metadata is copied.
    data = bytearray()
    def entry(name: str, mode: int, payload: bytes = b'') -> None:
        encoded = name.encode() + b'\0'
        fields = [1, mode, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(encoded), 0]
        data.extend(('070701' + ''.join(f'{v:08x}' for v in fields)).encode())
        data.extend(encoded)
        data.extend(b'\0' * (-len(data) % 4))
        data.extend(payload)
        data.extend(b'\0' * (-len(data) % 4))
    for file in sorted(root.rglob('*')):
        name = str(file.relative_to(root))
        if file.is_symlink():
            entry(name, 0o120777, os.readlink(file).encode())
        elif file.is_dir():
            entry(name, 0o040755)
        else:
            entry(name, 0o100755 if file.stat().st_mode & 0o111 else 0o100644, file.read_bytes())
    entry('TRAILER!!!', 0)
    data.extend(b'\0' * (-len(data) % 512))
    target.write_bytes(data)


def initrd(work: Path) -> Path:
    root = work / 'root'
    for d in ('bin', 'dev', 'proc', 'sys'):
        (root / d).mkdir(parents=True, exist_ok=True)
    busybox = shutil.which('busybox')
    if not busybox:
        raise Error('Disposable guest checks need busybox and a static-capable gcc.')
    shutil.copy2(busybox, root / 'bin/busybox')
    ldd = run('ldd', busybox, check=False)
    for path in set(re.findall(r'(/[^\s()]+)', ldd)):
        source = Path(path)
        if source.is_file():
            dest = root / source.relative_to('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    for app in ('sh', 'mount', 'base64', 'poweroff', 'sleep'):
        (root / 'bin' / app).symlink_to('busybox')
    code = work / 'probe.c'
    code.write_text(PROBE)
    run('gcc', '-static', '-O2', '-o', str(root / 'bin/probe'), str(code))
    script = root / 'init'
    script.write_text('''#!/bin/sh
export PATH=/bin
mount -t devtmpfs devtmpfs /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys
/bin/probe
for device in /sys/bus/pci/devices/*; do
  read -r vendor < "$device/vendor"
  read -r product < "$device/device"
  printf 'VMP_PCI:%s:%s\\n' "$vendor" "$product"
done
for file in /sys/firmware/acpi/tables/DSDT /sys/firmware/acpi/tables/APIC; do
  if [ -f "$file" ]; then
    name=${file##*/}
    printf 'VMP_TABLE_%s:' "$name"
    base64 -w 0 "$file"
    printf '\\n'
  fi
done
printf 'VMP_BOOT_OK\\n'
poweroff -f
''')
    script.chmod(0o755)
    archive = work / 'initrd.cpio'
    write_cpio(root, archive)
    return archive


def boot(binary: Path, firmware: Path, kernel: str, work: Path, advertised: bool,
         board_enabled: bool = True) -> dict:
    vars_template = Path('/usr/share/pve-edk2-firmware/OVMF_VARS_4M.fd')
    if not vars_template.is_file():
        raise Error('Stock blank 4 MiB VARS template missing.')
    vars_file = work / 'blank-vars.fd'
    shutil.copy2(vars_template, vars_file)
    image = initrd(work)
    args = [str(binary), '-machine', 'pc-q35-10.1+pve0,x-board-identity=' + ('on' if board_enabled else 'off'),
            '-accel', 'kvm', '-cpu', 'host,hypervisor=' + ('on,kvm=on' if advertised else 'off,kvm=off'),
            '-m', '256', '-smp', '2', '-nodefaults', '-display', 'none',
            '-nic', 'none', '-serial', 'stdio', '-no-reboot',
            '-device', 'pcie-root-port,id=test-port,bus=pcie.0,addr=0x1,slot=1',
            '-device', 'nec-usb-xhci,id=test-xhci,bus=pcie.0,addr=0x2',
            '-drive', f'if=pflash,format=raw,readonly=on,file={firmware}',
            '-drive', f'if=pflash,format=raw,file={vars_file}',
            '-kernel', '/boot/vmlinuz-' + kernel, '-initrd', str(image),
            '-append', 'console=ttyS0 rdinit=/init panic=-1 quiet']
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise Error('Disposable guest did not finish within 60 seconds.') from exc
    log = result.stdout + result.stderr
    (work / 'boot.log').write_text(log)
    probe = re.search(r'VMP_PROBE:(\{[^\r\n]+\})', log)
    if result.returncode or 'VMP_BOOT_OK' not in log or not probe:
        raise Error('Disposable guest failed to boot; inspect its private boot.log.')
    values = json.loads(probe.group(1))
    if values['hypervisor'] != int(advertised):
        raise Error('Disposable guest CPUID does not match its requested mode.')
    tables = {}
    for name, encoded in re.findall(r'VMP_TABLE_(DSDT|APIC):([A-Za-z0-9+/=]+)', log):
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) < 36 or sum(raw) % 256 or int.from_bytes(raw[4:8], 'little') != len(raw):
            raise Error('Disposable guest ACPI table length/checksum failed.')
        if name == 'DSDT':
            expected = (b'RTPI', b'RTAI') if board_enabled else (b'PRTP', b'PRTA')
            forbidden = (b'PRTP', b'PRTA') if board_enabled else (b'RTPI', b'RTAI')
            if any(n not in raw for n in expected) or any(n in raw for n in forbidden):
                raise Error('Routing names do not match the requested optional profile.')
        tables[name] = {'bytes': len(raw), 'checksum_valid': True}
    if set(tables) != {'DSDT', 'APIC'}:
        raise Error('Disposable guest did not expose both required ACPI tables.')
    pci = {(int(v, 16), int(d, 16)) for v, d in re.findall(r'VMP_PCI:(0x[0-9a-f]+):(0x[0-9a-f]+)', log)}
    expected_port = (0x8086, 0x3420) if board_enabled else (0x1b36, 0x000c)
    if expected_port not in pci or (0x1033, 0x0194) not in pci:
        raise Error('Disposable PCI root-port/controller identity check failed.')
    return {'booted': True, 'probe': values, 'acpi': tables,
            'board_enabled': board_enabled, 'pci_root_port_checked': True,
            'usb_controller_checked': True}


def validate_artifacts(directory: Path, recipe: dict, identity: dict,
                       kernel: str, runtime: bool = False) -> dict:
    manifest = check_artifacts(directory, recipe, identity)
    artifact = {k: directory / v['filename'] for k, v in manifest['artifacts'].items()}
    if run('dpkg-deb', '-f', str(artifact['qemu']), 'Package').strip() != 'pve-qemu-kvm' or run('dpkg-deb', '-f', str(artifact['qemu']), 'Version').strip() != recipe['qemu']['version']:
        raise Error('Candidate package identity/version mismatch.')
    if not run('modinfo', '-F', 'vermagic', str(artifact['kernel'])).startswith(kernel + ' '):
        raise Error('Candidate module kernel version mismatch.')
    if artifact['firmware'].stat().st_size != recipe['firmware']['code_size']:
        raise Error('Candidate firmware layout mismatch.')
    if runtime and (not Path('/sys/module/kvm/parameters/hidden_guest_ud').exists()
                    or Path('/sys/module/kvm/parameters/hidden_guest_ud').read_text().strip() != 'Y'):
        raise Error('Runtime verification needs the custom KVM option enabled.')
    with tempfile.TemporaryDirectory(prefix='vmpkit-check-', dir=directory.parent) as scratch:
        root = Path(scratch)
        run('dpkg-deb', '-x', str(artifact['qemu']), str(root / 'payload'))
        binary = root / 'payload/usr/bin/qemu-system-x86_64'
        # Select packaged ROM data; never borrow an unrelated binary's blobs.
        results = {}
        # The package places ROMs in /usr/share/kvm; use -L via a small local
        # wrapper so the boot helper cannot accidentally load installed ROMs.
        wrapper = root / 'qemu'
        import shlex
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' -L ' + shlex.quote(str(root / 'payload/usr/share/kvm')) + ' "$@"\n')
        wrapper.chmod(0o755)
        for advertised in (False, True):
            work = root / ('visible' if advertised else 'hidden')
            work.mkdir()
            try:
                values = boot(wrapper, artifact['firmware'], kernel, work, advertised)
            finally:
                if (work / 'boot.log').exists():
                    shutil.copy2(work / 'boot.log', directory / ('validation-' + work.name + '.log'))
            if runtime:
                signals = (values['probe']['vmcall_signal'], values['probe']['vmmcall_signal'])
                if not advertised and signals != (4, 4):
                    raise Error('Hidden-guest hypercalls did not both produce SIGILL.')
                if advertised and signals != (0, 11):
                    raise Error('Advertised-guest control changed from the tested behavior.')
            results['visible' if advertised else 'hidden'] = values
        work = root / 'profile-off'
        work.mkdir()
        try:
            results['profile_off'] = boot(wrapper, artifact['firmware'], kernel, work, True, False)
        finally:
            if (work / 'boot.log').exists():
                shutil.copy2(work / 'boot.log', directory / 'validation-profile-off.log')
        return {'schema': 1, 'recipe': recipe['id'], 'manifest_digest': digest(manifest),
                'kernel': kernel, 'guest_checks': results, 'runtime_hypercalls_checked': runtime,
                'passed': True}
