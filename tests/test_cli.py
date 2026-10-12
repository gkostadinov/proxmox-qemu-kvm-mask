import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.fixtures import host
from vmpkit.cli import main
from vmpkit.common import write_json
from vmpkit.plan import make_plan


class DryRunTests(unittest.TestCase):
    def test_apply_and_rollback_default_to_no_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); plan = make_plan(host(), '200')
            write_json(p / 'plan.json', plan)
            write_json(p / 'journal.json', {'plan': plan, 'phase': 'prepared'})
            with patch('vmpkit.cli.apply') as apply, patch('vmpkit.cli.rollback') as rollback:
                with contextlib.redirect_stdout(io.StringIO()) as stream:
                    rc = main(['apply', '--plan', str(p / 'plan.json'), '--artifacts', str(p / 'missing'), '--validation', str(p / 'missing.json')])
                self.assertEqual(rc, 0)
                self.assertTrue(json.loads(stream.getvalue())['dry_run'])
                apply.assert_not_called()
                with contextlib.redirect_stdout(io.StringIO()) as stream:
                    rc = main(['rollback', '--transaction', str(p)])
                self.assertEqual(rc, 0)
                self.assertTrue(json.loads(stream.getvalue())['dry_run'])
                rollback.assert_not_called()

    def test_build_dry_run_does_not_fetch_create_or_build(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); write_json(p / 'inventory.json', host())
            with patch('vmpkit.cli.build') as builder, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['build', '--snapshot', str(p / 'inventory.json'), '--work', str(p / 'work')]), 0)
            builder.assert_not_called()
            self.assertFalse((p / 'work').exists())

    def test_plan_output_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); write_json(p / 'inventory.json', host())
            self.assertEqual(main(['plan', '--snapshot', str(p / 'inventory.json'), '--vmid', '200', '--output', str(p / 'plan.json')]), 0)
            self.assertEqual((p / 'plan.json').stat().st_mode & 0o777, 0o600)
