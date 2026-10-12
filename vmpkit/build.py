# SPDX-License-Identifier: GPL-2.0-only
"""Pinned source preparation, builds, ABI checks and local module signing."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import urllib.request

from .common import Error, data_root, digest, read_json, run, sha, write_json
from .plan import identity_from_host, select_recipe, validate_identity

GIT_OPTIONS = ('-c', 'url.https://git.proxmox.com/git/.insteadOf=git://git.proxmox.com/git/')


def checkout(spec: dict, destination: Path, cache: Path | None = None) -> None:
    if destination.exists():
        if run('git', '-C', str(destination), 'rev-parse', 'HEAD').strip() != spec['commit']:
            raise Error('Existing source checkout has a different commit.')
        if run('git', '-C', str(destination), 'status', '--porcelain').strip():
            raise Error('Existing source checkout is dirty; use a fresh work directory.')
    elif cache:
        run('git', 'clone', '--shared', '--no-checkout', str(cache), str(destination))
        run('git', '-C', str(destination), 'remote', 'set-url', 'origin', spec['url'])
        run('git', '-C', str(destination), 'checkout', '--detach', spec['commit'])
    else:
        destination.mkdir(parents=True)
        run('git', 'init', str(destination))
        run('git', '-C', str(destination), 'remote', 'add', 'origin', spec['url'])
        run('git', *GIT_OPTIONS, '-C', str(destination), 'fetch', '--depth=1', 'origin', spec['commit'])
        run('git', '-C', str(destination), 'checkout', '--detach', 'FETCH_HEAD')
    sub = destination / spec['submodule']
    if not (sub / '.git').exists():
        cached_sub = cache / spec['submodule'] if cache else None
        if cached_sub and (cached_sub / '.git').exists():
            run('git', 'clone', '--shared', '--no-checkout', str(cached_sub), str(sub))
            run('git', '-C', str(sub), 'checkout', '--detach', spec['upstream_commit'])
        else:
            run('git', *GIT_OPTIONS, '-C', str(destination), 'submodule', 'update',
                '--init', '--depth=1', spec['submodule'])
    if run('git', '-C', str(sub), 'rev-parse', 'HEAD').strip() != spec['upstream_commit']:
        raise Error('Pinned upstream submodule commit mismatch.')


def copy_source(source: Path, target: Path) -> None:
    if target.exists():
        raise Error('Prepared source already exists; use a new work directory.')
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns('.git', '.gitmodules'))


def apply_patch(target: Path, text: str) -> None:
    run('patch', '--batch', '--fuzz=0', '--dry-run', '-p1', cwd=target, input=text)
    run('patch', '--batch', '--fuzz=0', '-p1', cwd=target, input=text)


def download_patch(spec: dict, work: Path) -> str:
    target = work / 'base-qemu.patch'
    if not target.exists():
        request = urllib.request.Request(spec['base_patch_url'], headers={'User-Agent': 'proxmox-qemu-kvm-mask'})
        with urllib.request.urlopen(request, timeout=60) as response:
            # Bound the download; this recipe's source patch is below 100 KiB.
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise Error('Source patch exceeds expected size limit.')
        target.write_bytes(body)
    if sha(target) != spec['base_patch_sha256']:
        raise Error('Base QEMU source patch digest mismatch.')
    chunks = target.read_text().split('diff --git ')
    return ''.join('diff --git ' + c for c in chunks[1:]
                   if not c.startswith('a/roms/seabios-hppa/'))


def prepare_qemu(repo: Path, recipe: dict, work: Path) -> Path:
    spec = recipe['qemu']
    source = repo / spec['build_dir']
    run('meson', 'subprojects', 'download', cwd=repo / spec['submodule'])
    copy_source(repo / spec['submodule'], source)
    shutil.copytree(repo / 'debian', source / 'debian')
    # Mirror the pinned Proxmox source preparation, including its blob purge.
    shutil.rmtree(source / 'roms/edk2', ignore_errors=True)
    shutil.rmtree(source / 'pc-bios/dtb', ignore_errors=True)
    purge = ('hppa-firmware', 'openbios-', 'palcode-clipper', 's390-ccw.img',
             's390-netboot.img', 'u-boot.e500', 'qemu_vga.ndrv', 'slof.bin', 'opensbi-riscv')
    for file in (source / 'pc-bios').rglob('*'):
        if file.is_file() and any(x in file.name for x in purge):
            file.unlink()
    meson = source / 'pc-bios/meson.build'
    meson.write_text(''.join(line for line in meson.read_text().splitlines(True)
                             if not any(x in line for x in purge) and "subdir('dtb')" not in line))
    (source / 'debian/SOURCE').write_text(f"{spec['url']}\ncommit {spec['commit']}\n")
    env = os.environ.copy() | {'QUILT_PATCHES': 'debian/patches'}
    run('quilt', 'push', '-a', cwd=source, env=env)
    apply_patch(source, download_patch(spec, work))
    for name in spec['patches']:
        apply_patch(source, (data_root() / 'patches' / name).read_text())
    # Original plain artwork; no upstream splash image or image library needed.
    splash = data_root() / 'assets/blank-splash.jpg'
    if sha(splash) != spec['splash_sha256']:
        raise Error('Original splash asset digest mismatch.')
    shutil.copy2(splash, source / 'pc-bios/bootsplash.jpg')
    changelog = source / 'debian/changelog'
    entry = (f"pve-qemu-kvm ({spec['version']}) trixie; urgency=medium\n\n"
             "  * Add optional board platform identity and routing names.\n\n"
             " -- Local Builder <builder@example.invalid>  Mon, 12 Oct 2026 00:00:00 +0000\n\n")
    changelog.write_text(entry + changelog.read_text())
    return source


def prepare_kernel(repo: Path, recipe: dict) -> Path:
    root = repo / 'kvm-build'
    root.mkdir()
    base = repo / recipe['kernel']['submodule']
    for rel in ('arch/x86/kvm', 'virt/kvm'):
        copy_source(base / rel, root / rel)
    selected = 0
    for patch in sorted((repo / 'patches/kernel').glob('*.patch')):
        blocks = patch.read_text().split('diff --git ')[1:]
        parts = [c for c in blocks if c.startswith(('a/arch/x86/kvm/', 'a/virt/kvm/'))]
        if parts:
            selected += 1
            apply_patch(root, ''.join('diff --git ' + c for c in parts))
    if selected != recipe['kernel']['selected_proxmox_patch_count']:
        raise Error('Matching Proxmox KVM patch count changed.')
    apply_patch(root, (data_root() / 'patches' / recipe['kernel']['patch']).read_text())
    makefile = root / 'arch/x86/kvm/Makefile'
    makefile.write_text('ccflags-y += -I $(src)\n' + makefile.read_text())
    # Trace headers are generated from an external module tree. These paths
    # are generated on the build machine, never shipped as personal paths.
    for rel in ('arch/x86/kvm/trace.h', 'arch/x86/kvm/mmu/mmutrace.h'):
        file = root / rel
        text, count = re.subn(r'^#define TRACE_INCLUDE_PATH .+$',
                             '#define TRACE_INCLUDE_PATH ' + str(file.parent),
                             file.read_text(), flags=re.M)
        if count != 1:
            raise Error('Unexpected KVM trace header layout.')
        file.write_text(text)
    return root


def prepare_firmware(repo: Path, recipe: dict, identity: dict) -> Path:
    sub = repo / recipe['firmware']['submodule']
    run('git', *GIT_OPTIONS, '-C', str(sub), 'submodule', 'update', '--init', '--recursive', '--depth=1')
    source = repo / 'firmware-build'
    copy_source(sub, source)
    shutil.copytree(repo / 'debian', source / 'debian')
    for name in (source / 'debian/patches/series').read_text().splitlines():
        if name.strip() and not name.startswith('#'):
            # EDK II sources and several packaging patches use CRLF. Preserve
            # patch bytes instead of normalizing line endings during decoding.
            apply_patch(source, (source / 'debian/patches' / name.strip()).read_bytes().decode())
    shutil.copy2(source / 'debian/Logo.bmp', source / 'MdeModulePkg/Logo/Logo.bmp')
    file = source / 'MdeModulePkg/MdeModulePkg.dec'
    text = file.read_text()
    for field, value in [('PcdFirmwareVendor', identity['bios_vendor']),
                         ('PcdFirmwareReleaseDateString', identity['bios_date'])]:
        text, count = re.subn(r'(' + field + r'\|)L"[^"\n]*"(\|VOID\*)',
                             lambda m: m[1] + 'L' + json.dumps(value) + m[2], text)
        if count != 1:
            raise Error('Unexpected firmware PCD schema.')
    file.write_text(text)
    return source


def parse_symvers(text: str) -> dict[str, tuple[str, str]]:
    result = {}
    for line in text.splitlines():
        row = line.split()
        if len(row) >= 3:
            result[row[1]] = (row[0].lower(), row[2])
    return result


def parse_imports(text: str) -> dict[str, str]:
    return {row[1]: row[0].lower() for line in text.splitlines()
            if len(row := line.split()) == 2}


def validate_abi(module: Path, exported: Path, headers: Path, kernel: str) -> dict:
    if not run('modinfo', '-F', 'vermagic', str(module)).startswith(kernel + ' '):
        raise Error('Module vermagic does not match the running kernel.')
    expected = parse_symvers((headers / 'Module.symvers').read_text())
    built = parse_symvers(exported.read_text())
    core_exports = {s: crc for s, (crc, owner) in built.items() if owner.endswith('/kvm') or owner == 'kvm'}
    expected_exports = {s: crc for s, (crc, owner) in expected.items() if owner.endswith('/kvm') or owner == 'kvm'}
    if not core_exports or core_exports != expected_exports:
        raise Error('KVM exported symbol set or CRC differs from the installed kernel headers.')
    imports = parse_imports(run('modprobe', '--dump-modversions', str(module)))
    if not imports or any(s not in expected or expected[s][0] != crc for s, crc in imports.items()):
        raise Error('KVM imported symbol CRC mismatch.')
    consumers = {}
    for name in ('kvm_intel', 'kvm_amd'):
        path = run('modinfo', '-n', name).strip()
        needs = parse_imports(run('modprobe', '--dump-modversions', path))
        if any(s in expected_exports and core_exports.get(s) != crc for s, crc in needs.items()):
            raise Error('Stock KVM consumer CRC mismatch.')
        consumers[name] = len(needs)
    return {'exports': len(core_exports), 'imports': len(imports),
            'consumer_imports': consumers, 'passed': True}


def sign_module(module: Path, headers: Path, key: Path, certificate: Path) -> None:
    if not key.is_file() or stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise Error('Signing key must be a local regular file inaccessible to group/others.')
    if not certificate.is_file():
        raise Error('Local DER signing certificate missing.')
    # mokutil may return a nonzero status even when its output says enrolled.
    output = run('mokutil', '--test-key', str(certificate), check=False).lower()
    if 'already enrolled' not in output:
        raise Error('Certificate is not confirmed enrolled. Enrollment is a separate manual operation.')
    run(str(headers / 'scripts/sign-file'), 'sha256', str(key), str(certificate), str(module))
    if not run('modinfo', '-F', 'signer', str(module)).strip():
        raise Error('Signed module has no readable signer.')


def run_edk2(source: Path, flags: list[str]) -> str:
    # A sourced script inherits its caller's positional arguments. Clear them
    # while initializing the environment, then pass flags only to the builder.
    return run('bash', '-c', 'set -e; vmp_args=("$@"); set --; source ./edksetup.sh; build "${vmp_args[@]}"',
               'firmware-build', *flags, cwd=source,
               env=os.environ.copy() | {'SOURCE_DATE_EPOCH': '1791763200'})


def build(host: dict, work: Path, identity: dict | None, jobs: int,
          components: list[str], cache: Path | None = None,
          key: Path | None = None, certificate: Path | None = None) -> dict:
    recipe = select_recipe(host)
    ident = validate_identity(identity) if identity else identity_from_host(host)
    work = work.resolve()
    if any(c.isspace() for c in str(work)):
        raise Error('KVM external build paths must not contain whitespace.')
    if jobs < 1 or jobs > 32:
        raise Error('Choose 1–32 build jobs.')
    headers = Path('/usr/src/linux-headers-' + host['kernel'])
    if 'kernel' in components and not (headers / 'Module.symvers').is_file():
        raise Error('Install exact matching kernel headers before building.')
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    outputs = work / 'artifacts'
    outputs.mkdir(exist_ok=True)
    manifest_file = outputs / 'manifest.json'
    manifest = read_json(manifest_file) if manifest_file.exists() else {
        'schema': 1, 'recipe': recipe['id'], 'recipe_digest': digest(recipe),
        'identity': ident, 'kernel': host['kernel'], 'artifacts': {}, 'abi': None,
        'signed': False, 'certificate_sha256': None,
        'patches': {p.name: sha(p) for p in (data_root() / 'patches').glob('*.patch')},
    }
    if manifest['recipe_digest'] != digest(recipe) or manifest['identity'] != ident:
        raise Error('Work directory belongs to a different build recipe or identity.')
    sources = work / 'sources'
    sources.mkdir(exist_ok=True)
    for component in components:
        print('Building ' + component, flush=True)
        repo_name = {'qemu': 'pve-qemu', 'kernel': 'pve-kernel', 'firmware': 'pve-edk2-firmware'}[component]
        repo = sources / repo_name
        checkout(recipe[component], repo, cache / repo_name if cache else None)
        if component == 'qemu':
            source = prepare_qemu(repo, recipe, work)
            run('dpkg-checkbuilddeps', cwd=source)
            run('dpkg-buildpackage', '-b', '-us', '-uc', cwd=source,
                env=os.environ.copy() | {'DEB_BUILD_OPTIONS': f'parallel={jobs}',
                                        'SOURCE_DATE_EPOCH': '1791763200'})
            source_artifact = repo / f"pve-qemu-kvm_{recipe['qemu']['version']}_amd64.deb"
            target = outputs / source_artifact.name
            shutil.copy2(source_artifact, target)
        elif component == 'kernel':
            source = prepare_kernel(repo, recipe)
            run('make', '-C', str(headers), 'M=' + str(source / 'arch/x86/kvm'),
                '-j' + str(jobs), 'modules',
                env=os.environ.copy() | {'KCFLAGS': f'-ffile-prefix-map={work}=/usr/src/vmpkit'})
            source_artifact = source / 'arch/x86/kvm/kvm.ko'
            manifest['abi'] = validate_abi(source_artifact, source / 'arch/x86/kvm/Module.symvers', headers, host['kernel'])
            target = outputs / 'kvm.ko'
            shutil.copy2(source_artifact, target)
            if key and certificate:
                sign_module(target, headers, key, certificate)
                manifest['signed'] = True
                manifest['certificate_sha256'] = sha(certificate)
            elif key or certificate:
                raise Error('Provide both --signing-key and --certificate.')
        else:
            source = prepare_firmware(repo, recipe, ident)
            run('make', '-C', str(source / 'BaseTools'), '-j' + str(jobs), 'ARCH=X64')
            flags = ['-n', str(jobs), '-a', 'IA32', '-a', 'X64', '-t', 'GCC',
                     '-p', recipe['firmware']['target'], '-b', 'RELEASE',
                     '-DNETWORK_HTTP_BOOT_ENABLE=TRUE', '-DNETWORK_IP6_ENABLE=TRUE',
                     '-DNETWORK_TLS_ENABLE', '-DSECURE_BOOT_ENABLE=TRUE', '-DPVSCSI_ENABLE=TRUE',
                     '-DTPM2_ENABLE=TRUE', '-DFD_SIZE_4MB', '-DSMM_REQUIRE=TRUE',
                     '-DFIRMWARE_VER=' + ident['bios_version'], '--pcd', 'PcdUninstallMemAttrProtocol=TRUE']
            run_edk2(source, flags)
            target = outputs / 'OVMF_CODE_4M.secboot.fd'
            shutil.copy2(source / 'Build/Ovmf3264/RELEASE_GCC/FV/OVMF_CODE.fd', target)
            if target.stat().st_size != recipe['firmware']['code_size']:
                raise Error('OVMF CODE size does not match the tested split-flash layout.')
        manifest['artifacts'][component] = {'filename': target.name, 'sha256': sha(target),
                                            'bytes': target.stat().st_size}
        write_json(manifest_file, manifest)
    return manifest


def check_artifacts(directory: Path, recipe: dict, identity: dict) -> dict:
    manifest = read_json(directory / 'manifest.json')
    if manifest['recipe_digest'] != digest(recipe) or manifest['identity'] != identity:
        raise Error('Artifacts do not match the selected recipe and identity.')
    if manifest.get('patches') != {p.name: sha(p) for p in (data_root() / 'patches').glob('*.patch')}:
        raise Error('Artifact source patches differ from this checkout.')
    if set(manifest['artifacts']) != {'qemu', 'kernel', 'firmware'} or not manifest.get('abi', {}).get('passed'):
        raise Error('Complete built and ABI-checked artifacts are required.')
    for value in manifest['artifacts'].values():
        if Path(value['filename']).name != value['filename']:
            raise Error('Artifact filename must be a single path component.')
        file = directory / value['filename']
        if sha(file) != value['sha256'] or file.stat().st_size != value['bytes']:
            raise Error('Artifact digest or size mismatch.')
    return manifest
