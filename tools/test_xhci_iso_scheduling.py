#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test the exact ISO scheduling function without USB hardware or a guest."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from test_usb_lifetime import extract, compile_c


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--expect-fail', action='store_true')
    parser.add_argument('--cc')
    args = parser.parse_args()
    template = Path(__file__).with_name('xhci_iso_contract.c.in').read_text()
    function = extract(args.source.read_text(), 'xhci_calc_iso_kick')
    with tempfile.TemporaryDirectory(prefix='xhci-iso-contract-') as directory:
        root = Path(directory)
        source = root / 'contract.c'
        binary = root / ('contract.exe' if os.name == 'nt' else 'contract')
        source.write_text(template.replace('/* EXTRACTED_FUNCTION */', function))
        compile_c(source, binary, args.cc)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        print(result.stdout, end='')
        if args.expect_fail:
            if result.returncode != 1 or 'contract assertion failed' not in result.stdout:
                raise SystemExit('Baseline must compile and fail scheduling assertions')
        elif result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
