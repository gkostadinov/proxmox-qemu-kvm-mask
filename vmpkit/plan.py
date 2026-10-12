# SPDX-License-Identifier: GPL-2.0-only
"""Exact recipe selection and UUID-preserving VM argument generation."""
from __future__ import annotations

import json
import re
import shlex
import uuid

from .common import Error, data_root, digest, read_json, sha


def select_recipe(host: dict) -> dict:
    for file in sorted((data_root() / 'recipes').glob('*.json')):
        recipe = read_json(file)
        m, p = recipe['match'], host['packages']
        if (host['architecture'] == m['architecture'] and host['kernel'] == m['kernel']
                and host['cpu_vendor'] == m['cpu_vendor'] and host.get('vmx')
                and p['pve-manager'] == m['pve_manager_version']
                and p['qemu-server'] == m['qemu_server_version']
                and p['pve-qemu-kvm'].split('+')[0] in m['qemu_base_versions']
                and p['pve-edk2-firmware-ovmf'] == m['ovmf_version']):
            return recipe
    raise Error('No tested recipe matches this host. No installation is permitted.')


def identity_from_host(host: dict) -> dict:
    dmi = host.get('dmi', {})
    value = {
        'bios_vendor': dmi.get('bios_vendor'), 'bios_version': dmi.get('bios_version'),
        'bios_date': dmi.get('bios_date'), 'board_vendor': dmi.get('board_vendor'),
        'board_name': dmi.get('board_name'), 'board_version': dmi.get('board_version') or '1.0',
        'system_vendor': dmi.get('sys_vendor') or dmi.get('board_vendor'),
        'cpu_model': host.get('cpu_model'),
    }
    return validate_identity(value)


def validate_identity(identity: dict) -> dict:
    keys = ('bios_vendor', 'bios_version', 'bios_date', 'board_vendor',
            'board_name', 'board_version', 'system_vendor', 'cpu_model')
    if set(identity) != set(keys):
        raise Error('Identity fields must match the documented schema; serials and UUIDs are excluded.')
    for key in keys:
        value = identity[key]
        if not isinstance(value, str) or not value or len(value) > 128 or not value.isascii() or any(ord(c) < 32 for c in value):
            raise Error(f'Invalid or unavailable identity field: {key}; provide --identity.')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,32}', identity['bios_version']):
        raise Error('BIOS version must be a simple firmware build token; provide an edited identity.')
    import datetime
    try:
        datetime.datetime.strptime(identity['bios_date'], '%m/%d/%Y')
    except ValueError as exc:
        raise Error('BIOS date must be MM/DD/YYYY.') from exc
    return identity


def vm_uuid(config: dict) -> str:
    fields = dict(item.split('=', 1) for item in config.get('smbios1', '').split(',') if '=' in item)
    try:
        return str(uuid.UUID(fields['uuid']))
    except (KeyError, ValueError) as exc:
        raise Error('The VM must already have a valid SMBIOS UUID. This tool never generates one.') from exc


def smbios_value(value: str) -> str:
    return value.replace(',', ',,')


def profile_args(config: dict, vmid: str, identity: dict,
                 generated_cpu: str | None = None, has_xhci: bool = False) -> str:
    old = shlex.split(config.get('args', ''))
    kept = []
    i = 0
    cpu_found = False
    while i < len(old):
        item = old[i]
        if item in ('-smbios', '-cpu'):
            if i + 1 == len(old):
                raise Error('Incomplete existing QEMU argument.')
            value = old[i + 1]
            if item == '-smbios':
                if value.startswith('type=1,'):
                    raise Error('Existing type-1 SMBIOS arguments require manual UUID review.')
                if re.match(r'^type=(0|2|3|4)(,|$)', value):
                    i += 2
                    continue
            else:
                if cpu_found or value.split(',')[0] != 'host':
                    raise Error('Only one host CPU override is supported.')
                cpu_found = True
                parts = [p for p in value.split(',') if not p.startswith(('hypervisor=', 'kvm='))]
                value = ','.join(parts + ['hypervisor=off', 'kvm=off'])
            kept.extend([item, value])
            i += 2
            continue
        if 'pflash0=' in item or 'x-board-identity' in item or item == '-blockdev':
            raise Error('An existing custom firmware/platform profile needs manual migration.')
        kept.append(item)
        i += 1
    if not cpu_found:
        if not generated_cpu or generated_cpu.split(',')[0] != 'host':
            raise Error('Generated host CPU arguments missing; capture a complete live inventory.')
        parts = [p for p in generated_cpu.split(',') if not p.startswith(('hypervisor=', 'kvm='))]
        parts = [p for p in parts if not p.startswith(('kvm-pv-eoi=', 'kvm-pv-unhalt='))]
        kept += ['-cpu', ','.join(parts + ['hypervisor=off', 'kvm=off', '-kvm-pv-eoi', '-kvm-pv-unhalt'])]
    for kind, fields in (
        (0, {'vendor': identity['bios_vendor'], 'version': identity['bios_version'], 'date': identity['bios_date']}),
        (2, {'manufacturer': identity['board_vendor'], 'product': identity['board_name'], 'version': identity['board_version']}),
        (3, {'manufacturer': identity['system_vendor']}),
        (4, {'manufacturer': 'Intel(R) Corporation', 'version': identity['cpu_model']}),
    ):
        value = ','.join([f'type={kind}'] + [k + '=' + smbios_value(v) for k, v in fields.items()])
        kept += ['-smbios', value]
    firmware = f'/usr/local/share/vmpkit/vm-{vmid}/OVMF_CODE_4M.secboot.fd'
    node = 'vmpkit-code'
    block = {'driver': 'raw', 'node-name': node, 'read-only': True,
             'file': {'driver': 'file', 'filename': firmware}}
    kept += ['-blockdev', json.dumps(block, separators=(',', ':')),
             '-machine', f'pflash0={node},x-board-identity=on']
    if has_xhci:
        kept += ['-set', 'device.xhci.driver=nec-usb-xhci']
    return shlex.join(kept)


def make_plan(host: dict, vmid: str, identity: dict | None = None) -> dict:
    recipe = select_recipe(host)
    if not str(vmid).isdigit() or vmid not in host.get('guests', {}):
        raise Error('Select an existing local VM with --vmid.')
    if host.get('running_containers'):
        raise Error('Running containers detected. This first release does not manage container downtime.')
    if host.get('ha_guests'):
        raise Error('HA-managed guests need manual lifecycle coordination before this first-release installer.')
    config = host['guests'][vmid]['config']
    if (config.get('machine', '').split(',')[0] not in ('pc-q35-10.1', 'pc-q35-10.1+pve0') or config.get('bios') != 'ovmf'
            or 'efitype=4m' not in config.get('efidisk0', '')
            or config.get('ostype') not in ('win10', 'win11')
            or config.get('cpu', '').split(',')[0] != 'host'
            or not any(re.fullmatch(r'sata\d+', k) for k in config)):
        raise Error('Target must use q35 10.1, 4 MiB OVMF, host CPU, Windows and existing SATA storage.')
    if any(k in config for k in ('lock', 'template', 'hookscript')):
        raise Error('Locked/template/hook-managed VMs require manual review.')
    ident = validate_identity(identity) if identity is not None else identity_from_host(host)
    value = {'schema': 1, 'recipe': recipe['id'], 'recipe_digest': digest(recipe),
             'patch_digests': {p.name: sha(p) for p in (data_root() / 'patches').glob('*.patch')},
             'vmid': vmid, 'kernel': host['kernel'],
             'packages_before': host['packages'], 'package_held_before': host.get('package_held', False),
             'original_config': config, 'config_digest': digest(config),
             'uuid': vm_uuid(config), 'identity': ident,
             'qemu_cpu_before': host['guests'][vmid].get('qemu_cpu'),
             'args_after': profile_args(config, vmid, ident, host['guests'][vmid].get('qemu_cpu'),
                                        host['guests'][vmid].get('has_xhci', False)),
             'running_before': [v for v, g in host['guests'].items() if g['status'] == 'running'],
             'guest_states': {v: g['status'] for v, g in host['guests'].items()},
             'needs_all_vm_downtime': True,
             'changes': ['QEMU package', 'signed KVM core for current kernel',
                         'VM-specific OVMF CODE', 'target VM args', 'conditional boot service'],
             'preserved': ['SMBIOS UUID', 'disks', 'EFI variables', 'TPM state',
                           'NIC MACs', 'CPU allocation', 'passthrough devices', 'other VM configurations'],
             'experimental': True}
    value['approval_digest'] = digest(value)
    return value


def verify_plan(plan: dict) -> None:
    contents = {k: v for k, v in plan.items() if k != 'approval_digest'}
    if digest(contents) != plan.get('approval_digest'):
        raise Error('Plan digest does not match its contents.')
