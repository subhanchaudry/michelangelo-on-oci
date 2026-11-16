import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
import runtime


class RenewalImageTests(unittest.TestCase):
    def core(self):
        return types.SimpleNamespace(
            RELEASE=Path(__file__).resolve().parents[1] / 'installer/release',
            run=Mock(return_value='synthetic-token'), secret=Mock(), apply=Mock())

    def test_private_image_is_pullable_and_python_command_overrides_identity_entrypoint(self):
        core = self.core()
        image = 'registry.example.test/runtime@sha256:' + 'a' * 64
        with patch.object(runtime, 'core', core):
            runtime.token_registration('test-manager', 'test-cluster', 'test-ca', image)
        documents = core.apply.call_args.args[0]
        job = next(item for item in documents if item['kind'] == 'CronJob')
        pod = job['spec']['jobTemplate']['spec']['template']['spec']
        self.assertEqual(pod['containers'][0]['image'], image)
        self.assertEqual(pod['containers'][0]['command'], ['python', '-c'])
        self.assertEqual(pod['imagePullSecrets'], [{'name': 'ma-lifecycle-runtime-registry'}])

    def test_mutable_image_does_not_rotate_an_existing_registration(self):
        core = self.core()
        with patch.object(runtime, 'core', core), self.assertRaises(RuntimeError):
            runtime.token_registration('test-manager', 'test-cluster', 'test-ca', 'python:latest')
        core.run.assert_not_called()
        core.secret.assert_not_called()
        core.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
