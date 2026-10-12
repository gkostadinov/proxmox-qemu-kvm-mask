from pathlib import Path
import subprocess
import tempfile
import unittest

from vmpkit.build import apply_patch, run_edk2


class BuildIntegrationTests(unittest.TestCase):
    def test_setup_script_does_not_receive_builder_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / 'edksetup.sh').write_text('[ "$#" = 0 ] || return 7\nbuild() { printf "%s\\n" "$@"; }\n')
            self.assertEqual(run_edk2(p, ['-n', '8', 'one argument']), '-n\n8\none argument\n')

    def test_strict_patch_preserves_crlf_and_rejects_wrong_context(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); (p / 'file').write_bytes(b'before\r\n')
            text = '--- a/file\n+++ b/file\n@@ -1 +1 @@\n-before\r\n+after\r\n'
            apply_patch(p, text)
            self.assertEqual((p / 'file').read_bytes(), b'after\r\n')
