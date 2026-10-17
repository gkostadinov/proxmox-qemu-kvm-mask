# SPDX-License-Identifier: GPL-2.0-only
"""Check strict USB patch application and baseline/patched source contracts."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'c3d48b7d1e89604920e5b81b91140c2ad39a1943'
SOURCE_SHA256 = '2d8800a97aed333db7476584db0b7f51e3d35a018fad90ac8b173b9cf2d9e9b7'
URL = f'https://raw.githubusercontent.com/qemu/qemu/{COMMIT}/hw/usb/host-libusb.c'


def main():
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(128 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise SystemExit('Pinned USB source hash mismatch')
    with tempfile.TemporaryDirectory(prefix='usb-lifetime-patch-') as directory:
        root = Path(directory)
        source = root / 'hw/usb/host-libusb.c'
        source.parent.mkdir(parents=True)
        source.write_bytes(data)
        harness = ROOT / 'tools/test_usb_lifetime.py'
        subprocess.run([sys.executable, str(harness), str(source), '--expect-fail'], check=True)
        patch = (ROOT / 'patches/qemu-usb-lifetime.patch').read_text()
        for extra in (['--dry-run'], []):
            subprocess.run(['patch', '--batch', '--fuzz=0', *extra, '-p1'],
                           cwd=root, input=patch, text=True, check=True)
        subprocess.run([sys.executable, str(harness), str(source)], check=True)


if __name__ == '__main__':
    main()
