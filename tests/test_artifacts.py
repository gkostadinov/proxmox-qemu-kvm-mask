from pathlib import Path
import tempfile
import unittest

from tests.fixtures import host
from vmpkit.build import check_artifacts
from vmpkit.common import Error, digest, sha, write_json
from vmpkit.plan import make_plan, select_recipe


class ArtifactTests(unittest.TestCase):
    def test_manifest_requires_complete_matching_untampered_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); plan = make_plan(host(), '200'); recipe = select_recipe(host())
            artifacts = {}
            for name in ('qemu', 'kernel', 'firmware'):
                f = p / name; f.write_bytes(name.encode())
                artifacts[name] = {'filename': name, 'sha256': sha(f), 'bytes': f.stat().st_size}
            manifest = {'recipe_digest': digest(recipe), 'identity': plan['identity'],
                        'patches': plan['patch_digests'], 'artifacts': artifacts, 'abi': {'passed': True}}
            write_json(p / 'manifest.json', manifest)
            self.assertEqual(check_artifacts(p, recipe, plan['identity']), manifest)
            (p / 'kernel').write_bytes(b'tampered')
            with self.assertRaises(Error): check_artifacts(p, recipe, plan['identity'])
            (p / 'kernel').write_bytes(b'kernel')
            manifest['patches'] = {}; write_json(p / 'manifest.json', manifest)
            with self.assertRaises(Error): check_artifacts(p, recipe, plan['identity'])
            manifest['patches'] = plan['patch_digests']
            manifest['artifacts']['qemu']['filename'] = '../outside'
            write_json(p / 'manifest.json', manifest)
            with self.assertRaises(Error): check_artifacts(p, recipe, plan['identity'])
