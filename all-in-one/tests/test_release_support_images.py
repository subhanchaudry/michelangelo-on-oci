import tempfile
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

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

    def test_custom_proxy_preserves_auth_for_private_shutdown_helper(self):
        with patch('operator_images.prepare'), patch.object(install,'run'), \
             patch.object(install,'apply') as apply, \
             patch.object(install.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)):
            install.prerequisites({}, {'delivery':{'pinned':'registry.example/platform@sha256:'+'a'*64}})
        proxy=next(call.args[0] for call in apply.call_args_list if call.args[0].get('kind')=='EnvoyProxy')
        kubernetes=proxy['spec']['provider']['kubernetes']
        self.assertEqual(kubernetes['envoyDeployment']['pod']['imagePullSecrets'],[{'name':'michelangelo-registry'}])
        self.assertEqual(kubernetes['envoyService']['type'],'ClusterIP')

    def test_missing_support_build_cannot_reach_cluster_mutation(self):
        with self.assertRaisesRegex(RuntimeError, 'required before deployment'):
            install.deploy({}, Path('/unused'), 'database.example.invalid', {})


if __name__ == '__main__':
    unittest.main()
