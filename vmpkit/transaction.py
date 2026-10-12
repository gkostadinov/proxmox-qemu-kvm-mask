# SPDX-License-Identifier: GPL-2.0-only
"""Root-only deployment journals and restoration of the exact previous state."""
from __future__ import annotations

from contextlib import contextmanager
import datetime
import fcntl
import os
from pathlib import Path
import shutil
import uuid

from .build import check_artifacts
from .common import Error, digest, read_json, require_root, run, sha, write_json
from .detect import inventory, parse_config
from .plan import select_recipe, verify_plan, vm_uuid
from .verify import validate_artifacts

HELPER_PATH = Path('/usr/local/libexec/vmpkit-kvm-enable')
SERVICE_PATH = Path('/etc/systemd/system/vmpkit-kvm.service')
LOCK_PATH = Path('/run/lock/vmpkit.lock')


def module_path(kernel: str) -> Path:
    return Path('/lib/modules') / kernel / 'updates/vmpkit/kvm.ko'


def firmware_path(vmid: str) -> Path:
    return Path(f'/usr/local/share/vmpkit/vm-{vmid}/OVMF_CODE_4M.secboot.fd')


def initramfs_path(kernel: str) -> Path:
    return Path('/boot/initrd.img-' + kernel)


@contextmanager
def locked(state: Path):
    require_root()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink() or state.stat().st_uid != 0 or state.stat().st_mode & 0o077:
        raise Error('State directory must be root-owned, mode 0700, and not a symlink.')
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if info.st_uid != 0 or info.st_mode & 0o077:
        os.close(fd)
        raise Error('Global transaction lock must be root-owned and mode 0600.')
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def guest_status(vmid: str) -> str:
    return run('qm', 'status', vmid).strip().split(': ', 1)[-1]


def all_stopped(ids: list[str]) -> None:
    current = inventory()
    if set(current['guests']) != set(ids) or current['running_containers'] or current.get('ha_guests'):
        raise Error('Guest inventory changed during transaction.')
    if any(guest_status(v) != 'stopped' for v in ids):
        raise Error('All VMs must be stopped before changing shared QEMU/KVM components.')


def stop_guests(ids: list[str]) -> None:
    for vmid in ids:
        if guest_status(vmid) == 'running':
            run('qm', 'shutdown', vmid, '--timeout', '180')
            if guest_status(vmid) != 'stopped':
                raise Error('Graceful shutdown failed; forced poweroff is never used.')


def restore_running(ids: list[str], running: list[str]) -> None:
    # Preserve stopped guests and restart only the original running set.
    for vmid in running:
        if vmid not in ids:
            raise Error('Original guest disappeared; refusing a guessed restart.')
        if guest_status(vmid) == 'stopped':
            run('qm', 'start', vmid)


def event(directory: Path, journal: dict, name: str, **updates) -> None:
    journal.update(updates)
    journal['phase'] = name
    journal['events'].append({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'phase': name})
    write_json(directory / 'journal.json', journal)


def install_service(kernel: str) -> None:
    HELPER_PATH.parent.mkdir(parents=True, exist_ok=True)
    HELPER_PATH.write_text('#!/bin/sh\nset -eu\n'
                          f'[ "$(uname -r)" = "{kernel}" ] || exit 0\n'
                          'modprobe kvm\n'
                          'parameter=/sys/module/kvm/parameters/hidden_guest_ud\n'
                          '[ ! -w "$parameter" ] || printf Y > "$parameter"\n')
    HELPER_PATH.chmod(0o755)
    SERVICE_PATH.write_text('[Unit]\nDescription=Enable the optional virtual platform KVM behavior\n'
                            'After=local-fs.target\nBefore=pve-guests.service\n\n'
                            '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                            f'ExecStart={HELPER_PATH}\n\n[Install]\nWantedBy=multi-user.target\n')
    run('systemd-analyze', 'verify', str(SERVICE_PATH))
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', SERVICE_PATH.name)


def package_backup(path: Path | None, version: str, directory: Path) -> Path:
    if path is None:
        # This only downloads; dependencies are never installed or upgraded.
        run('apt-get', 'download', 'pve-qemu-kvm=' + version, cwd=directory)
        files = list(directory.glob('pve-qemu-kvm_*.deb'))
        if len(files) != 1:
            raise Error('Exact rollback package unavailable; provide --rollback-package.')
        path = files[0]
    if (run('dpkg-deb', '-f', str(path), 'Package').strip() != 'pve-qemu-kvm'
            or run('dpkg-deb', '-f', str(path), 'Version').strip() != version):
        raise Error('Rollback package must match the currently installed version exactly.')
    target = directory / 'qemu-before.deb'
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
    return target


def preflight(plan: dict, artifacts: Path, validation: dict, approve: str,
              experimental: bool) -> tuple[dict, dict, dict]:
    verify_plan(plan)
    if approve != plan['approval_digest']:
        raise Error('Execution needs --approve with the reviewed plan digest.')
    if not experimental:
        raise Error('This first recipe is experimental; execution requires --allow-experimental.')
    host = inventory()
    recipe = select_recipe(host)
    if (recipe['id'] != plan['recipe'] or digest(recipe) != plan['recipe_digest']
            or host['packages'] != plan['packages_before']
            or host['package_held'] != plan['package_held_before']
            or host['running_containers'] or host.get('ha_guests')
            or {v: g['status'] for v, g in host['guests'].items()} != plan['guest_states']
            or digest(host['guests'][plan['vmid']]['config']) != plan['config_digest']):
        raise Error('Host or VM state changed since planning. Generate and inspect a new plan.')
    if 'kernel' not in Path(host['kvm_path']).parts or host['kvm_parameter_present']:
        raise Error('An existing custom KVM core needs manual migration. This installer starts from stock KVM.')
    manifest = check_artifacts(artifacts, recipe, plan['identity'])
    if manifest.get('patches') != plan.get('patch_digests'):
        raise Error('Artifacts do not use the source patches in the reviewed plan.')
    if not validation.get('passed') or validation.get('manifest_digest') != digest(manifest) or validation.get('kernel') != host['kernel']:
        raise Error('A fresh successful isolated validation of these exact artifacts is required.')
    if not manifest['signed'] or not run('modinfo', '-F', 'signer', str(artifacts / manifest['artifacts']['kernel']['filename'])).strip():
        raise Error('An enrolled-certificate signed KVM core is required; signature enforcement is never disabled.')
    return host, recipe, manifest


def apply(plan: dict, artifacts: Path, validation: dict, state: Path,
          approve: str, experimental: bool, rollback_package: Path | None) -> Path:
    with locked(state):
        host, recipe, manifest = preflight(plan, artifacts, validation, approve, experimental)
        directory = state / ('transaction-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        backup = directory / 'backup'
        backup.mkdir(mode=0o700)
        kernel, vmid = host['kernel'], plan['vmid']
        module = module_path(kernel)
        firmware = firmware_path(vmid)
        if any(p.exists() for p in (module, firmware, HELPER_PATH, SERVICE_PATH)):
            raise Error('Deployment paths already exist; refusing to overwrite untracked state.')
        previous = package_backup(rollback_package, host['packages']['pve-qemu-kvm'], backup)
        # Back up configurations for review; restoration changes only owned args.
        for guest in host['guests']:
            shutil.copy2(Path('/etc/pve/qemu-server') / (guest + '.conf'), backup / (guest + '.conf'))
        initramfs = initramfs_path(kernel)
        shutil.copy2(initramfs, backup / initramfs.name)
        journal = {'schema': 1, 'plan': plan, 'manifest': manifest,
                   'rollback_package_sha256': sha(previous), 'events': [],
                   'module_path': str(module), 'firmware_path': str(firmware),
                   'kernel_touched': False, 'package_touched': False,
                   'args_touched': False, 'service_touched': False}
        event(directory, journal, 'prepared')
        try:
            event(directory, journal, 'stopping-original-running-set')
            stop_guests(plan['running_before'])
            all_stopped(list(host['guests']))
            event(directory, journal, 'replacing-kvm', kernel_touched=True)
            run('modprobe', '-r', 'kvm_intel')
            run('modprobe', '-r', 'kvm')  # never force unload
            module.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifacts / manifest['artifacts']['kernel']['filename'], module)
            module.chmod(0o644)
            run('depmod', '-a', kernel)
            run('modprobe', 'kvm', 'hidden_guest_ud=1')
            run('modprobe', 'kvm_intel')
            if sha(module) != manifest['artifacts']['kernel']['sha256'] or run('modinfo', '-n', 'kvm').strip() != str(module):
                raise Error('Custom KVM core did not resolve to the intended module.')
            runtime = validate_artifacts(artifacts, recipe, plan['identity'], kernel, runtime=True)
            write_json(directory / 'runtime-validation.json', runtime)
            all_stopped(list(host['guests']))
            event(directory, journal, 'installing-qemu', package_touched=True)
            run('dpkg', '-i', str(artifacts / manifest['artifacts']['qemu']['filename']))
            run('apt-mark', 'hold', 'pve-qemu-kvm')
            firmware.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifacts / manifest['artifacts']['firmware']['filename'], firmware)
            firmware.chmod(0o644)
            event(directory, journal, 'setting-vm-profile', args_touched=True)
            run('qm', 'set', vmid, '--args', plan['args_after'])
            current = parse_config(run('qm', 'config', vmid))
            expected = plan['original_config'] | {'args': plan['args_after']}
            if current != expected or vm_uuid(current) != plan['uuid']:
                raise Error('VM UUID or unowned configuration changed.')
            for guest in host['guests']:
                if guest != vmid and parse_config(run('qm', 'config', guest)) != host['guests'][guest]['config']:
                    raise Error('Another VM configuration changed during installation.')
            event(directory, journal, 'enabling-persistence', service_touched=True)
            install_service(kernel)
            run('update-initramfs', '-u', '-k', kernel)
            event(directory, journal, 'restarting-original-running-set')
            restore_running(list(host['guests']), plan['running_before'])
            event(directory, journal, 'installed-awaiting-guest-verification')
        except BaseException:
            event(directory, journal, 'failed-attempting-rollback')
            try:
                restore(directory, journal)
            except BaseException as exc:
                event(directory, journal, 'rollback-needs-attention')
                raise Error('Automatic restoration could not finish. Preserve the journal and use rollback after resolving the reported failure.') from exc
            raise
        return directory


def restore(directory: Path, journal: dict) -> None:
    plan = journal['plan']
    kernel, vmid = plan['kernel'], plan['vmid']
    if journal['module_path'] != str(module_path(kernel)) or journal['firmware_path'] != str(firmware_path(vmid)):
        raise Error('Transaction-owned paths do not match its kernel and VM.')
    if run('uname', '-r').strip() != kernel:
        raise Error('Boot the transaction kernel before restoring its loaded module.')
    if sha(directory / 'backup/qemu-before.deb') != journal['rollback_package_sha256']:
        raise Error('Rollback package changed after backup.')
    if journal.get('args_touched'):
        current = parse_config(run('qm', 'config', vmid))
        if current.get('args', '') not in (plan['args_after'], plan['original_config'].get('args', '')) or vm_uuid(current) != plan['uuid']:
            raise Error('VM args or UUID changed externally. Review them before rollback downtime.')
    ids = list(plan['guest_states'])
    if journal.get('kernel_touched') or journal.get('package_touched'):
        stop_guests(ids)
        all_stopped(ids)
    if journal.get('args_touched'):
        current = parse_config(run('qm', 'config', vmid))
        if current.get('args', '') not in (plan['args_after'], plan['original_config'].get('args', '')):
            raise Error('VM args were edited after installation. Review them before rollback.')
        if vm_uuid(current) != plan['uuid']:
            raise Error('VM UUID changed externally; rollback will not overwrite it.')
        event(directory, journal, 'restoring-vm-args')
        if 'args' in plan['original_config']:
            run('qm', 'set', vmid, '--args', plan['original_config']['args'])
        elif 'args' in current:
            run('qm', 'set', vmid, '--delete', 'args')
    if journal.get('service_touched'):
        event(directory, journal, 'removing-owned-persistence')
        run('systemctl', 'disable', '--now', SERVICE_PATH.name, check=False)
        SERVICE_PATH.unlink(missing_ok=True)
        HELPER_PATH.unlink(missing_ok=True)
        run('systemctl', 'daemon-reload')
    if journal.get('package_touched'):
        event(directory, journal, 'restoring-qemu')
        run('dpkg', '-i', str(directory / 'backup/qemu-before.deb'))
        run('apt-mark', 'hold' if plan['package_held_before'] else 'unhold', 'pve-qemu-kvm')
    if journal.get('kernel_touched'):
        event(directory, journal, 'restoring-stock-kvm')
        run('modprobe', '-r', 'kvm_intel')
        run('modprobe', '-r', 'kvm')
        Path(journal['module_path']).unlink(missing_ok=True)
        run('depmod', '-a', kernel)
        run('modprobe', 'kvm_intel')
        resolved = run('modinfo', '-n', 'kvm').strip()
        if 'kernel' not in Path(resolved).parts:
            raise Error('Stock KVM module resolution was not restored.')
        # Restore the backed-up initramfs atomically, including its metadata.
        initramfs = initramfs_path(kernel)
        temporary = initramfs.with_name(initramfs.name + '.vmpkit-restore')
        shutil.copy2(directory / 'backup' / initramfs.name, temporary)
        os.replace(temporary, initramfs)
    Path(journal['firmware_path']).unlink(missing_ok=True)
    event(directory, journal, 'restoring-original-running-set')
    restore_running(ids, plan['running_before'])
    if vm_uuid(parse_config(run('qm', 'config', vmid))) != plan['uuid']:
        raise Error('Final UUID verification failed.')
    event(directory, journal, 'rolled-back')


def rollback(directory: Path, state: Path) -> None:
    with locked(state):
        if directory.is_symlink() or directory.parent.resolve() != state.resolve():
            raise Error('Transaction must be directly inside the state directory.')
        journal = read_json(directory / 'journal.json')
        verify_plan(journal['plan'])
        if journal['phase'] == 'rolled-back':
            return
        restore(directory, journal)
