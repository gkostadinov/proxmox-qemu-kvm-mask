import copy
import json
from pathlib import Path
import shlex
import unittest

from tests.fixtures import host
from vmpkit.common import Error
from vmpkit.detect import parse_config, redact
from vmpkit.plan import make_plan, profile_args, select_recipe, validate_identity, verify_plan


class PlanningTests(unittest.TestCase):
    def test_exact_recipe_rejects_unknown_kernel_cpu_and_versions(self):
        self.assertEqual(select_recipe(host())['id'], 'pve9-intel-7.0.14-22')
        for field, value in [('kernel', '7.0.14-23-pve'), ('cpu_vendor', 'AuthenticAMD'),
                             ('architecture', 'arm64'), ('vmx', False)]:
            h = host(); h[field] = value
            with self.assertRaises(Error): select_recipe(h)
        for package, version in [('pve-qemu-kvm', '11.2.0-1'), ('pve-edk2-firmware-ovmf', '4.2027.01-1'), ('pve-manager', '10.0.0')]:
            h = host(); h['packages'][package] = version
            with self.assertRaises(Error): select_recipe(h)

    def test_no_uuid_generation_and_no_unowned_config_mutation(self):
        h = host(); original = copy.deepcopy(h)
        p = make_plan(h, '200')
        self.assertEqual(h, original)
        self.assertEqual(p['uuid'], original['guests']['200']['config']['smbios1'][5:])
        self.assertNotIn('type=1', p['args_after'])
        self.assertEqual(p['running_before'], ['200'])
        verify_plan(p)
        del h['guests']['200']['config']['smbios1']
        with self.assertRaises(Error): make_plan(h, '200')

    def test_plan_tampering_and_snapshot_parser(self):
        p = make_plan(host(), '200'); p['args_after'] += ' changed'
        with self.assertRaises(Error): verify_plan(p)
        self.assertEqual(parse_config('args: first\n[snapshot]\nargs: historical\n'), {'args': 'first'})
        with self.assertRaises(Error): parse_config('cpu: host\ncpu: other\n')

    def test_identity_quotes_are_arguments_not_shell_code(self):
        p = make_plan(host(), '200')
        identity = p['identity'] | {'board_vendor': 'Example, Corporation', 'board_name': "Board '$(printf example)'"}
        args = shlex.split(profile_args(p['original_config'], '200', identity, p['qemu_cpu_before'], True))
        self.assertIn("type=2,manufacturer=Example,, Corporation,product=Board '$(printf example)',version=1.0", args)
        self.assertEqual(sum(a.startswith('type=0,') for a in args), 1)
        self.assertIn('vmport=off', args)
        self.assertIn('device.xhci.driver=nec-usb-xhci', args)
        self.assertIn('host,hv-relaxed,hv-vapic,hypervisor=off,kvm=off,-kvm-pv-eoi,-kvm-pv-unhalt', args)
        with self.assertRaises(Error): validate_identity(identity | {'serial': 'example'})
        with self.assertRaises(Error): validate_identity(identity | {'bios_vendor': 'line\nbreak'})

    def test_existing_profile_and_type_one_override_are_blocked(self):
        for extra in (' -machine x-board-identity=on', ' -blockdev anything', ' -smbios type=1,uuid=example'):
            h = host(); h['guests']['200']['config']['args'] += extra
            with self.assertRaises(Error): make_plan(h, '200')

    def test_running_containers_and_locked_vms_are_blocked(self):
        h = host(); h['running_containers'] = ['300']
        with self.assertRaises(Error): make_plan(h, '200')
        h = host(); h['guests']['200']['config']['lock'] = 'backup'
        with self.assertRaises(Error): make_plan(h, '200')
        h = host(); h['ha_guests'] = ['200']
        with self.assertRaises(Error): make_plan(h, '200')
        h = host(); h['guests']['200']['config']['machine'] = 'pc-q35-11.0'
        with self.assertRaises(Error): make_plan(h, '200')

    def test_redaction_is_allowlisted(self):
        h = host(); h['hostname'] = 'private-host'; h['dmi']['board_serial'] = 'private-serial'
        h['packages']['pve-qemu-kvm'] += '+private-build'
        text = json.dumps(redact(h))
        for value in ('private-host', 'private-serial', 'private-build', '11111111', 'example-system', 'host=1-2'):
            self.assertNotIn(value, text)
        self.assertIn('11.1.1-2+local', text)
