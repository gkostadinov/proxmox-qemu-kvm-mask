# SPDX-License-Identifier: GPL-2.0-only
"""Small process and state helpers; subprocess arguments never use a shell."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class Error(RuntimeError):
    pass


def run(*args: str, cwd: Path | None = None, env: dict | None = None,
        input: str | None = None, check: bool = True) -> str:
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                            input=input, text=True, capture_output=True)
    if check and result.returncode:
        # Command arguments may contain a local key path or VM identity. Keep
        # diagnostics in the local terminal, never in published build receipts.
        raise Error(f"{args[0]} failed ({result.returncode}): {(result.stderr or result.stdout)[-1800:]}")
    return result.stdout


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def data_root() -> Path:
    source = Path(__file__).resolve().parent.parent
    if (source / 'recipes').is_dir():
        return source
    installed = Path(sys.prefix) / 'share/vmpkit'
    if (installed / 'recipes').is_dir():
        return installed
    raise Error('Recipe data missing; use a complete checkout or install its wheel.')


def require_root() -> None:
    if os.geteuid() != 0:
        raise Error('Deployment and rollback execution require root.')
