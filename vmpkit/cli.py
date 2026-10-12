# SPDX-License-Identifier: GPL-2.0-only
"""Public CLI. Deployment and restoration default to dry-run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .build import build
from .common import Error, read_json, write_json
from .detect import inventory, redact
from .plan import identity_from_host, make_plan, select_recipe, verify_plan
from .transaction import apply, rollback
from .verify import validate_artifacts


def output(value: dict, path: Path | None = None) -> None:
    if path:
        write_json(path, value)
    else:
        print(json.dumps(value, indent=2, sort_keys=True))


def host_for(args) -> dict:
    return read_json(args.snapshot) if args.snapshot else inventory()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog='qemu-mask', description='Proxmox QEMU/KVM/OVMF marker masking with version-matched builds and reversible deployment.')
    sub = root.add_subparsers(dest='command', required=True)
    detect = sub.add_parser('detect', help='read-only system detection; output redacted by default')
    detect.add_argument('--private', action='store_true', help='include local VM configuration for offline planning; never publish this file')
    detect.add_argument('--identity-only', action='store_true')
    detect.add_argument('--output', type=Path)
    plan = sub.add_parser('plan', help='write an inspectable private local installation plan')
    plan.add_argument('--vmid', required=True)
    plan.add_argument('--identity', type=Path)
    plan.add_argument('--snapshot', type=Path)
    plan.add_argument('--output', type=Path)
    builder = sub.add_parser('build', help='show pinned builds; --execute builds only in the work directory')
    builder.add_argument('--snapshot', type=Path)
    builder.add_argument('--identity', type=Path)
    builder.add_argument('--work', type=Path, default=Path('work'))
    builder.add_argument('--jobs', type=int, default=8)
    builder.add_argument('--components', nargs='+', choices=['qemu', 'kernel', 'firmware'], default=['qemu', 'kernel', 'firmware'])
    builder.add_argument('--source-cache', type=Path, help='optional local pinned source mirrors; not required')
    builder.add_argument('--signing-key', type=Path)
    builder.add_argument('--certificate', type=Path)
    mode = builder.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    verify = sub.add_parser('verify', help='validate built artifacts using disposable Linux guests')
    verify.add_argument('--snapshot', type=Path)
    verify.add_argument('--artifacts', type=Path, required=True)
    verify.add_argument('--runtime', action='store_true', help='also require live custom KVM hypercall behavior')
    verify.add_argument('--output', type=Path)
    install = sub.add_parser('apply', help='review deployment; --execute requires the exact approved plan')
    install.add_argument('--plan', type=Path, required=True)
    install.add_argument('--artifacts', type=Path, required=True)
    install.add_argument('--validation', type=Path, required=True)
    install.add_argument('--state', type=Path, default=Path('/var/lib/vmpkit'))
    install.add_argument('--rollback-package', type=Path)
    install.add_argument('--approve')
    install.add_argument('--allow-experimental', action='store_true')
    mode = install.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    restore = sub.add_parser('rollback', help='review or execute restoration from a transaction journal')
    restore.add_argument('--transaction', type=Path, required=True)
    restore.add_argument('--state', type=Path, default=Path('/var/lib/vmpkit'))
    mode = restore.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    status = sub.add_parser('status', help='read a private local transaction journal')
    status.add_argument('--transaction', type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'detect':
            host = inventory()
            value = identity_from_host(host) if args.identity_only else (host if args.private else redact(host))
            output(value, args.output)
        elif args.command == 'plan':
            output(make_plan(host_for(args), args.vmid,
                             read_json(args.identity) if args.identity else None), args.output)
        elif args.command == 'build':
            host = host_for(args)
            recipe = select_recipe(host)
            identity = read_json(args.identity) if args.identity else identity_from_host(host)
            if not args.execute:
                output({'dry_run': True, 'recipe': recipe['id'], 'identity': identity,
                        'sources': {k: {'url': recipe[k]['url'], 'commit': recipe[k]['commit'],
                                        'upstream_commit': recipe[k]['upstream_commit']} for k in args.components},
                        'work_directory': str(args.work), 'components': args.components,
                        'system_packages_installed': False,
                        'signed_build_requested': bool(args.signing_key and args.certificate),
                        'next': 'Inspect the recipe and build dependencies; add --execute to build.'})
            else:
                value = build(host, args.work, identity, args.jobs, args.components,
                              args.source_cache, args.signing_key, args.certificate)
                output({'built': list(value['artifacts']), 'manifest': str(args.work / 'artifacts/manifest.json')})
        elif args.command == 'verify':
            host = host_for(args)
            recipe = select_recipe(host)
            manifest = read_json(args.artifacts / 'manifest.json')
            output(validate_artifacts(args.artifacts, recipe, manifest['identity'],
                                      host['kernel'], args.runtime), args.output)
        elif args.command == 'apply':
            plan = read_json(args.plan)
            verify_plan(plan)
            if not args.execute:
                output({'dry_run': True, 'vmid': plan['vmid'], 'recipe': plan['recipe'],
                        'approval_digest': plan['approval_digest'], 'changes': plan['changes'],
                        'preserved': plan['preserved'], 'running_set_to_stop_and_restart': plan['running_before'],
                        'args_before': plan['original_config'].get('args', ''), 'args_after': plan['args_after'],
                        'rollback_package': str(args.rollback_package) if args.rollback_package else 'download exact installed version',
                        'requires': ['root', 'reviewed digest', 'experimental opt-in', 'signed complete artifacts',
                                     'successful isolated validation', 'unchanged host and VM state', 'stock current KVM'],
                        'host_changes_executed': False})
            else:
                directory = apply(plan, args.artifacts.resolve(), read_json(args.validation),
                                  args.state.resolve(), args.approve, args.allow_experimental, args.rollback_package)
                output({'transaction': str(directory), 'result': 'installed; complete guest verification before declaring success'})
        elif args.command == 'rollback':
            journal = read_json(args.transaction / 'journal.json')
            verify_plan(journal['plan'])
            if not args.execute:
                output({'dry_run': True, 'phase': journal['phase'], 'vmid': journal['plan']['vmid'],
                        'original_running_set': journal['plan']['running_before'],
                        'steps': ['graceful VM shutdown when shared components changed', 'restore owned VM args preserving UUID',
                                  'restore exact QEMU package and prior hold policy', 'restore stock KVM and initramfs',
                                  'remove owned CODE and persistence helper', 'restart original running set'],
                        'guest_variables_restored': False, 'host_changes_executed': False})
            else:
                rollback(args.transaction.resolve(), args.state.resolve())
                output({'result': 'rolled back', 'transaction': str(args.transaction)})
        else:
            journal = read_json(args.transaction / 'journal.json')
            output({'phase': journal['phase'], 'events': journal['events'], 'vmid': journal['plan']['vmid']})
        return 0
    except (Error, OSError, KeyError, ValueError) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
