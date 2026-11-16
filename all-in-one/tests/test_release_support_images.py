import tempfile
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
import install
import release_support_images as support


class ReleaseSupportTests(unittest.TestCase):
    def test_changed_source_tag_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            core = types.SimpleNamespace(WORK=Path(directory), run=Mock(return_value='f' * 40))
            with self.assertRaisesRegex(RuntimeError, 'pinned commit'):
                support.source(core, 'temporal')

    def test_dirty_source_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            commit = support.SOURCES['temporal'][2]
            (Path(directory) / ('support-source-temporal-' + commit)).mkdir()
            core = types.SimpleNamespace(WORK=Path(directory), run=Mock(side_effect=[commit, '?? injected.go']))
            with self.assertRaisesRegex(RuntimeError, 'unreviewed changes'):
                support.source(core, 'temporal')

    def test_missing_support_build_cannot_reach_cluster_mutation(self):
        with self.assertRaisesRegex(RuntimeError, 'required before deployment'):
            install.deploy({}, Path('/unused'), 'database.example.invalid', {})


if __name__ == '__main__':
    unittest.main()
