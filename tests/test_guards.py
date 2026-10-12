import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.fixtures import host
from vmpkit.build import parse_imports, parse_symvers, sign_module
from vmpkit.common import Error, digest
from vmpkit.plan import make_plan, select_recipe
from vmpkit.transaction import preflight, restore_running, stop_guests


class GuardTests(unittest.TestCase):
    def test_vermagic_and_crc_parsers(self):
        self.assertEqual(parse_imports('0x12345678 symbol\n'), {'symbol': '0x12345678'})
        self.assertEqual(parse_symvers('0x12345678 symbol arch/x86/kvm/kvm EXPORT_SYMBOL_GPL\n')['symbol'], ('0x12345678', 'arch/x86/kvm/kvm'))

    def test_signing_key_permissions_checked_without_reading_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); key = p / 'key'; key.write_text('synthetic test data'); key.chmod(0o644)
            with patch('vmpkit.build.run') as run:
                with self.assertRaises(Error): sign_module(p / 'module', p / 'headers', key, p / 'certificate')
                run.assert_not_called()

    def test_drift_and_existing_custom_core_block_deployment(self):
        p = make_plan(host(), '200')
        for mutate in (lambda h: h['guests']['200']['config'].update(memory='16384'),
                       lambda h: h['guests']['201'].update(status='running'),
                       lambda h: h.update(kvm_parameter_present=True)):
            h = host(); mutate(h)
            with patch('vmpkit.transaction.inventory', return_value=h), patch('vmpkit.transaction.check_artifacts') as artifact:
                with self.assertRaises(Error): preflight(p, Path('unused'), {}, p['approval_digest'], True)
                artifact.assert_not_called()

    def test_approval_and_experimental_opt_in_are_required(self):
        p = make_plan(host(), '200')
        with patch('vmpkit.transaction.inventory') as inventory:
            with self.assertRaises(Error): preflight(p, Path('unused'), {}, 'wrong', True)
            with self.assertRaises(Error): preflight(p, Path('unused'), {}, p['approval_digest'], False)
            inventory.assert_not_called()

    def test_failed_graceful_shutdown_never_forces_poweroff(self):
        calls = []
        with patch('vmpkit.transaction.guest_status', return_value='running'), patch('vmpkit.transaction.run', side_effect=lambda *a, **k: calls.append(a)):
            with self.assertRaises(Error): stop_guests(['200'])
        self.assertEqual(calls, [('qm', 'shutdown', '200', '--timeout', '180')])

    def test_only_original_running_guests_restart(self):
        calls = []
        with patch('vmpkit.transaction.guest_status', return_value='stopped'), patch('vmpkit.transaction.run', side_effect=lambda *a, **k: calls.append(a)):
            restore_running(['200', '201'], ['200'])
        self.assertEqual(calls, [('qm', 'start', '200')])
