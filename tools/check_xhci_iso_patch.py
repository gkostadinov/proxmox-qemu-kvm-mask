# SPDX-License-Identifier: GPL-2.0-only
"""Check strict ISO scheduling patch application and source contracts."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'c3d48b7d1e89604920e5b81b91140c2ad39a1943'
SOURCE_SHA256 = '561518aebfcbfa1a86313d550956f682de06ca5f90d904812e07693de8565cd8'
URL = f'https://raw.githubusercontent.com/qemu/qemu/{COMMIT}/hw/usb/hcd-xhci.c'


def main():
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(128 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise SystemExit('Pinned USB source hash mismatch')
    with tempfile.TemporaryDirectory(prefix='xhci-iso-patch-') as directory:
        root = Path(directory)
        source = root / 'hw/usb/hcd-xhci.c'
        source.parent.mkdir(parents=True)
        source.write_bytes(data)
        harness = ROOT / 'tools/test_xhci_iso_scheduling.py'
        subprocess.run([sys.executable, str(harness), str(source), '--expect-fail'], check=True)
        patch = (ROOT / 'patches/qemu-xhci-iso-scheduling.patch').read_text()
        for extra in (['--dry-run'], []):
            subprocess.run(['patch', '--batch', '--fuzz=0', *extra, '-p1'],
                           cwd=root, input=patch, text=True, check=True)
        subprocess.run([sys.executable, str(harness), str(source)], check=True)


if __name__ == '__main__':
    main()
