#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile exact host-libusb functions against ordinary error-contract mocks.

No USB devices, QEMU process, corruption, invalid pointers, or I/O are used.
Examples:
  python test_usb_lifetime.py host-libusb.parent-patched.c
  python test_usb_lifetime.py host-libusb.original.c --expect-fail
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def extract(source, name):
    match = re.search(r'^static void\s+' + re.escape(name) + r'\s*\(', source, re.M)
    if not match:
        raise ValueError(f'Function not found: {name}')
    # Mask comments and literals without changing character offsets.
    mask = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: ' ' * len(m.group()), source, flags=re.S)
    opening = mask.index('{', match.start())
    depth = 1
    end = opening + 1
    while depth:
        depth += (mask[end] == '{') - (mask[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def compile_c(path, output, compiler):
    if compiler:
        cc = shutil.which(compiler) or compiler
    else:
        cc = next((shutil.which(n) for n in ('cc', 'clang', 'gcc') if shutil.which(n)), None)
    if cc:
        command = [cc, '-std=c11', '-Wall', str(path), '-o', str(output)]
        subprocess.run(command, check=True)
        return
    if os.name == 'nt':
        vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
        if vswhere.exists():
            installation = subprocess.check_output([str(vswhere), '-latest', '-property', 'installationPath'], text=True).strip()
            vcvars = Path(installation) / 'VC/Auxiliary/Build/vcvars64.bat'
            if vcvars.exists():
                batch = path.with_suffix('.cmd')
                batch.write_text('@echo off\n'
                                 f'call "{vcvars}" >nul\n'
                                 'if errorlevel 1 exit /b 1\n'
                                 f'cl /nologo /W3 /TC "{path}" /Fe:"{output}" /Fo:"{output.with_suffix(".obj")}"\n')
                subprocess.run(['cmd.exe', '/d', '/c', str(batch)], check=True)
                return
    raise RuntimeError('No C compiler found; use --cc /path/to/cc or a Visual Studio developer shell')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--cc')
    parser.add_argument('--expect-fail', action='store_true', help='require ordinary assertion failures from baseline source')
    parser.add_argument('--emit', type=Path, help='also retain the generated C translation unit')
    args = parser.parse_args()
    source = args.source.read_text()
    print(f'Source SHA256: {hashlib.sha256(args.source.read_bytes()).hexdigest()}', flush=True)
    names = ['usb_host_req_abort', 'usb_host_unrealize', 'usb_host_handle_control', 'usb_host_handle_data']
    functions = '\n'.join(extract(source, name) for name in names)
    template = Path(__file__).with_name('usb_lifetime_contract.c.in').read_text()
    generated = template.replace('/* EXTRACTED_FUNCTIONS */', functions)
    if args.emit:
        args.emit.write_text(generated)
    with tempfile.TemporaryDirectory(prefix='usb-lifetime-contract-') as directory:
        root = Path(directory)
        cfile = root / 'contract.c'
        exe = root / ('contract.exe' if os.name == 'nt' else 'contract')
        cfile.write_text(generated)
        compile_c(cfile, exe, args.cc)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        print(result.stdout, end='')
        print(result.stderr, end='')
        if args.expect_fail:
            if result.returncode != 1 or 'contract assertion failed' not in result.stdout:
                raise SystemExit('Baseline must compile and fail ordinary contract assertions')
            print('Expected baseline contract failures confirmed.')
        elif result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
