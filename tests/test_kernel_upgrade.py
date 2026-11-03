import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.fixtures import host
from vmpkit.build import build, build_host, check_artifacts, validate_abi
from vmpkit.cli import main
from vmpkit.common import Error, data_root, digest, sha, write_json
from vmpkit.plan import identity_from_host, select_recipe


RUNNING_KERNEL = '7.0.14-22-pve'
TARGET_KERNEL = '7.0.14-26-pve'


def patch_digests():
    return {p.name: sha(p) for p in (data_root() / 'patches').glob('*.patch')}


class KernelUpgradeTests(unittest.TestCase):
    def test_fixed_kernel_recipe_selects_exact_sources_and_patch(self):
        recipe = select_recipe(host() | {'kernel': TARGET_KERNEL})
        self.assertEqual(recipe['id'], 'pve9-intel-7.0.14-26')
        self.assertEqual(recipe['kernel']['commit'],
                         '78f3d3e2f3ce28c51b80919bc3fc35a399ddb3c7')
        self.assertEqual(recipe['kernel']['upstream_commit'],
                         '49ecb900ee95547cf6e887aba22a5ce52c617b78')
        self.assertEqual(recipe['kernel']['selected_proxmox_patch_count'], 40)
        self.assertEqual(recipe['kernel']['patch'], 'kvm-hypercall-7.0.14-26.patch')
        self.assertTrue((data_root() / 'patches' / recipe['kernel']['patch']).is_file())
        self.assertEqual(select_recipe(host())['id'], 'pve9-intel-7.0.14-22')
        with self.assertRaises(Error):
            select_recipe(host() | {'kernel': '7.0.14-23-pve'})

    def test_ported_patch_preserves_all_custom_code_changes(self):
        def changes(name):
            return [line for line in (data_root() / 'patches' / name).read_text().splitlines()
                    if line.startswith(('+', '-')) and not line.startswith(('+++', '---'))]
        self.assertEqual(changes('kvm-hypercall-7.0.14-26.patch'),
                         changes('kvm-hypercall.patch'))

    def test_preboot_target_dry_run_selects_26_without_changes(self):
        running = host()
        original = copy.deepcopy(running)
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / 'not-created'
            with (patch('vmpkit.cli.host_for', return_value=running),
                  patch('vmpkit.cli.build') as builder,
                  patch('vmpkit.build.checkout') as checkout,
                  patch('vmpkit.build.run') as command,
                  contextlib.redirect_stdout(io.StringIO()) as stream):
                rc = main(['build', '--components', 'kernel', '--target-kernel', TARGET_KERNEL,
                           '--work', str(work)])
            self.assertEqual(rc, 0)
            result = json.loads(stream.getvalue())
            self.assertTrue(result['dry_run'])
            self.assertEqual(result['running_kernel'], RUNNING_KERNEL)
            self.assertEqual(result['target_kernel'], TARGET_KERNEL)
            self.assertEqual(result['recipe'], 'pve9-intel-7.0.14-26')
            self.assertEqual(set(result['sources']), {'kernel'})
            self.assertEqual(result['sources']['kernel']['commit'],
                             '78f3d3e2f3ce28c51b80919bc3fc35a399ddb3c7')
            self.assertFalse(result['system_packages_installed'])
            self.assertFalse(work.exists())
            self.assertEqual(list(Path(d).iterdir()), [])
            builder.assert_not_called()
            checkout.assert_not_called()
            command.assert_not_called()
        self.assertEqual(running, original)

    def test_target_kernel_requires_kernel_only_build(self):
        running = host()
        original = copy.deepcopy(running)
        target = build_host(running, ['kernel'], TARGET_KERNEL)
        self.assertEqual(target['kernel'], TARGET_KERNEL)
        self.assertIsNot(target, running)
        for components in (['qemu'], ['firmware'], ['kernel', 'qemu'],
                           ['qemu', 'kernel', 'firmware'], ['kernel', 'kernel'], []):
            with self.subTest(components=components):
                with self.assertRaisesRegex(Error, 'requires --components kernel'):
                    build_host(running, components, TARGET_KERNEL)
        self.assertEqual(running, original)

    def test_execute_passes_original_running_host_and_explicit_target(self):
        running = host()
        original = copy.deepcopy(running)
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / 'not-created'
            with (patch('vmpkit.cli.host_for', return_value=running),
                  patch('vmpkit.cli.build', return_value={'artifacts': {'kernel': {}}}) as builder,
                  contextlib.redirect_stdout(io.StringIO()) as stream):
                rc = main(['build', '--components', 'kernel', '--target-kernel', TARGET_KERNEL,
                           '--work', str(work), '--execute'])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(stream.getvalue())['built'], ['kernel'])
            builder.assert_called_once()
            args, kwargs = builder.call_args
            self.assertIs(args[0], running)
            self.assertEqual(args[0]['kernel'], RUNNING_KERNEL)
            self.assertEqual(args[1], work)
            self.assertEqual(args[4], ['kernel'])
            self.assertEqual(kwargs, {'target_kernel': TARGET_KERNEL})
            self.assertFalse(work.exists())
        self.assertEqual(running, original)

    def test_abi_checks_stock_consumers_for_target_kernel(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            module = root / 'kvm.ko'
            exported = root / 'Module.symvers'
            headers = root / 'headers'
            headers.mkdir()
            (headers / 'Module.symvers').write_text(
                '0x11111111 core_symbol arch/x86/kvm/kvm EXPORT_SYMBOL_GPL\n'
                '0x22222222 host_symbol vmlinux EXPORT_SYMBOL\n')
            exported.write_text('0x11111111 core_symbol arch/x86/kvm/kvm EXPORT_SYMBOL_GPL\n')
            consumers = {name: str(root / TARGET_KERNEL / (name + '.ko'))
                         for name in ('kvm_intel', 'kvm_amd')}
            responses = {
                ('modinfo', '-F', 'vermagic', str(module)): TARGET_KERNEL + ' SMP modversions',
                ('modprobe', '--dump-modversions', str(module)): '0x22222222 host_symbol\n',
            }
            for name, path in consumers.items():
                responses[('modinfo', '-k', TARGET_KERNEL, '-n', name)] = path
                responses[('modprobe', '--dump-modversions', path)] = '0x11111111 core_symbol\n'

            def run(*args, **kwargs):
                self.assertFalse(kwargs)
                self.assertIn(args, responses, 'Unexpected command could resolve the running kernel')
                return responses[args]

            with patch('vmpkit.build.run', side_effect=run) as command:
                result = validate_abi(module, exported, headers, TARGET_KERNEL)
            self.assertEqual(result, {'exports': 1, 'imports': 1,
                                      'consumer_imports': {'kvm_intel': 1, 'kvm_amd': 1},
                                      'passed': True})
            for name in consumers:
                command.assert_any_call('modinfo', '-k', TARGET_KERNEL, '-n', name)

            responses[('modprobe', '--dump-modversions', consumers['kvm_intel'])] = (
                '0xdeadbeef core_symbol\n')
            with patch('vmpkit.build.run', side_effect=run):
                with self.assertRaisesRegex(Error, 'consumer CRC mismatch'):
                    validate_abi(module, exported, headers, TARGET_KERNEL)

            responses[('modinfo', '-F', 'vermagic', str(module))] = RUNNING_KERNEL + ' SMP'
            with patch('vmpkit.build.run', side_effect=run):
                with self.assertRaisesRegex(Error, 'target kernel'):
                    validate_abi(module, exported, headers, TARGET_KERNEL)

    def test_stale_patch_manifest_rejected_before_source_preparation(self):
        running = host()
        recipe = select_recipe(running | {'kernel': TARGET_KERNEL})
        identity = identity_from_host(running)
        stale_patches = patch_digests()
        stale_patches['kvm-hypercall-7.0.14-26.patch'] = '0' * 64
        original_is_file = Path.is_file
        headers_symvers = Path('/usr/src/linux-headers-' + TARGET_KERNEL + '/Module.symvers')
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            manifest_file = work / 'artifacts/manifest.json'
            write_json(manifest_file, {'recipe_digest': digest(recipe), 'identity': identity,
                                       'patches': stale_patches})

            def is_file(path):
                return path == headers_symvers or original_is_file(path)

            with (patch('vmpkit.build.Path.is_file', autospec=True, side_effect=is_file),
                  patch('vmpkit.build.checkout') as checkout,
                  patch('vmpkit.build.prepare_kernel') as prepare,
                  patch('vmpkit.build.run') as command):
                with self.assertRaisesRegex(Error, 'source patches changed'):
                    build(running, work, None, 1, ['kernel'], target_kernel=TARGET_KERNEL)
            checkout.assert_not_called()
            prepare.assert_not_called()
            command.assert_not_called()
            self.assertFalse((work / 'sources').exists())

    def test_kernel_only_manifest_cannot_be_used_for_full_deployment(self):
        recipe = select_recipe(host() | {'kernel': TARGET_KERNEL})
        identity = identity_from_host(host())
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            module = root / 'kvm.ko'
            module.write_bytes(b'synthetic kernel artifact')
            write_json(root / 'manifest.json', {
                'recipe_digest': digest(recipe), 'identity': identity,
                'patches': patch_digests(), 'abi': {'passed': True}, 'signed': True,
                'artifacts': {'kernel': {'filename': module.name, 'sha256': sha(module),
                                          'bytes': module.stat().st_size}},
            })
            with self.assertRaisesRegex(Error, 'Complete built and ABI-checked artifacts'):
                check_artifacts(root, recipe, identity)
