from contextlib import ExitStack
from contextlib import nullcontext
import copy
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import patch

from tests.fixtures import host
from vmpkit.common import Error, sha
from vmpkit.plan import make_plan, select_recipe
from vmpkit.transaction import apply, restore


class RollbackTests(unittest.TestCase):
    def test_apply_failure_automatically_restores_original_running_set(self):
        for failure in ('runtime', 'package'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                root = Path(d); state = root / 'state'; state.mkdir()
                artifacts = root / 'artifacts'; artifacts.mkdir()
                for name in ('kernel', 'qemu', 'firmware'):
                    (artifacts / name).write_bytes(name.encode())
                manifest = {'artifacts': {name: {'filename': name, 'sha256': sha(artifacts / name)}
                                         for name in ('kernel', 'qemu', 'firmware')}}
                module = root / 'installed-module'; firmware = root / 'installed-code'
                initrd = root / 'initrd'; initrd.write_bytes(b'original-initramfs')
                h = host(); plan = make_plan(h, '200')
                statuses = {'200': 'running', '201': 'stopped'}; calls = []
                def command(*args, **kwargs):
                    calls.append(args)
                    if args[0] == 'uname': return plan['kernel']
                    if args[:2] == ('qm', 'shutdown'): statuses[args[2]] = 'stopped'
                    if args[:2] == ('qm', 'start'): statuses[args[2]] = 'running'
                    if args[:2] == ('qm', 'config'):
                        return ''.join(k + ': ' + v + '\n' for k,v in h['guests'][args[2]]['config'].items())
                    if args[:2] == ('modinfo', '-n'):
                        return str(module) if module.exists() else '/lib/modules/example/kernel/kvm.ko'
                    if args[0] == 'dpkg' and args[-1] == str(artifacts / 'qemu'):
                        raise Error('simulated package failure')
                    return ''
                original_copy = shutil.copy2
                def copy_file(src, dst, *a, **kw):
                    if str(src).startswith('/etc/pve/qemu-server/'):
                        Path(dst).write_text('synthetic config backup')
                        return dst
                    return original_copy(src, dst, *a, **kw)
                def archive(path, version, directory):
                    target = directory / 'qemu-before.deb'; target.write_bytes(b'original-package'); return target
                with ExitStack() as stack:
                    stack.enter_context(patch('vmpkit.transaction.locked', return_value=nullcontext()))
                    stack.enter_context(patch('vmpkit.transaction.preflight', return_value=(h, select_recipe(h), manifest)))
                    stack.enter_context(patch('vmpkit.transaction.package_backup', side_effect=archive))
                    for name, path in [('module_path', module), ('firmware_path', firmware), ('initramfs_path', initrd)]:
                        stack.enter_context(patch('vmpkit.transaction.' + name, return_value=path))
                    stack.enter_context(patch('vmpkit.transaction.HELPER_PATH', root / 'helper'))
                    stack.enter_context(patch('vmpkit.transaction.SERVICE_PATH', root / 'service'))
                    stack.enter_context(patch('vmpkit.transaction.shutil.copy2', side_effect=copy_file))
                    stack.enter_context(patch('vmpkit.transaction.run', side_effect=command))
                    stack.enter_context(patch('vmpkit.transaction.guest_status', side_effect=lambda v: statuses[v]))
                    stack.enter_context(patch('vmpkit.transaction.all_stopped', side_effect=lambda ids: self.assertTrue(all(statuses[v] == 'stopped' for v in ids))))
                    if failure == 'runtime':
                        stack.enter_context(patch('vmpkit.transaction.validate_artifacts', side_effect=Error('simulated runtime failure')))
                    else:
                        stack.enter_context(patch('vmpkit.transaction.validate_artifacts', return_value={'passed': True}))
                    with self.assertRaisesRegex(Error, 'simulated'):
                        apply(plan, artifacts, {}, state, plan['approval_digest'], True, None)
                self.assertFalse(module.exists())
                self.assertEqual(statuses, {'200': 'running', '201': 'stopped'})
                self.assertEqual(initrd.read_bytes(), b'original-initramfs')
                import json
                journal = json.loads(next(state.glob('transaction-*/journal.json')).read_text())
                self.assertEqual(journal['phase'], 'rolled-back')
                if failure == 'package':
                    self.assertIn(('apt-mark', 'unhold', 'pve-qemu-kvm'), calls)

    def test_restores_exact_package_args_hold_initramfs_and_running_set(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); backup = directory / 'backup'; backup.mkdir()
            (backup / 'qemu-before.deb').write_bytes(b'original-package')
            module = directory / 'module'; module.write_bytes(b'custom-module')
            firmware = directory / 'firmware'; firmware.write_bytes(b'custom-code')
            initrd = directory / 'initrd'; initrd.write_bytes(b'new-initramfs')
            (backup / 'initrd').write_bytes(b'original-initramfs')
            helper = directory / 'helper'; helper.write_text('owned helper')
            service = directory / 'service'; service.write_text('owned service')
            plan = make_plan(host(), '200')
            plan['package_held_before'] = True
            config = plan['original_config'] | {'args': plan['args_after']}
            states = {'200': 'running', '201': 'stopped'}
            calls = []
            def command(*args, **kwargs):
                calls.append(args)
                if args[:2] == ('uname', '-r'): return plan['kernel'] + '\n'
                if args[:2] == ('qm', 'config'): return ''.join(k + ': ' + v + '\n' for k,v in config.items())
                if args[:2] == ('qm', 'set'): config['args'] = args[4]
                if args[:2] == ('qm', 'shutdown'): states[args[2]] = 'stopped'
                if args[:2] == ('qm', 'start'): states[args[2]] = 'running'
                if args[:2] == ('modinfo', '-n'): return '/lib/modules/example/kernel/arch/x86/kvm/kvm.ko\n'
                return ''
            journal = {'plan': plan, 'events': [], 'phase': 'installed',
                       'rollback_package_sha256': sha(backup / 'qemu-before.deb'),
                       'module_path': str(module), 'firmware_path': str(firmware),
                       'kernel_touched': True, 'package_touched': True,
                       'args_touched': True, 'service_touched': True}
            with ExitStack() as stack:
                for name, value in [('module_path', module), ('firmware_path', firmware), ('initramfs_path', initrd)]:
                    stack.enter_context(patch('vmpkit.transaction.' + name, return_value=value))
                stack.enter_context(patch('vmpkit.transaction.HELPER_PATH', helper))
                stack.enter_context(patch('vmpkit.transaction.SERVICE_PATH', service))
                stack.enter_context(patch('vmpkit.transaction.run', side_effect=command))
                stack.enter_context(patch('vmpkit.transaction.guest_status', side_effect=lambda v: states[v]))
                stack.enter_context(patch('vmpkit.transaction.all_stopped', side_effect=lambda ids: self.assertTrue(all(states[v] == 'stopped' for v in ids))))
                restore(directory, journal)
            self.assertEqual(config, plan['original_config'])
            self.assertEqual(states, {'200': 'running', '201': 'stopped'})
            self.assertEqual(initrd.read_bytes(), b'original-initramfs')
            self.assertFalse(any(p.exists() for p in (module, firmware, helper, service)))
            self.assertIn(('dpkg', '-i', str(backup / 'qemu-before.deb')), calls)
            self.assertIn(('apt-mark', 'hold', 'pve-qemu-kvm'), calls)
            self.assertNotIn(('qm', 'start', '201'), calls)
            self.assertEqual(journal['phase'], 'rolled-back')

    def test_changed_args_are_rejected_before_downtime(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); (directory / 'backup').mkdir()
            (directory / 'backup/qemu-before.deb').write_bytes(b'original-package')
            p = make_plan(host(), '200'); config = p['original_config'] | {'args': 'external change'}
            journal = {'plan': p, 'args_touched': True, 'kernel_touched': True,
                       'module_path': str(directory / 'module'), 'firmware_path': str(directory / 'firmware'),
                       'rollback_package_sha256': sha(directory / 'backup/qemu-before.deb')}
            def run(*args, **kwargs):
                if args[0] == 'uname': return p['kernel']
                return ''.join(k + ': ' + v + '\n' for k,v in config.items())
            with patch('vmpkit.transaction.module_path', return_value=directory / 'module'), patch('vmpkit.transaction.firmware_path', return_value=directory / 'firmware'), patch('vmpkit.transaction.run', side_effect=run), patch('vmpkit.transaction.stop_guests') as stop:
                with self.assertRaises(Error): restore(directory, journal)
                stop.assert_not_called()
