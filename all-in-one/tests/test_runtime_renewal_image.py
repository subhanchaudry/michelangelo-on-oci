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


    def test_upgrade_installs_new_policy_and_pull_credentials_before_renewal(self):
        import base64
        import io
        import json
        import tempfile
        import workload_security
        events = []
        def apply(documents):
            for document in documents if isinstance(documents, list) else [documents]:
                if document.get('kind') == 'CronJob':
                    events.append('renewal')
        def run(args, **kwargs):
            if 'get' in args and 'secret' in args:
                return json.dumps({'data': {'.dockerconfigjson': base64.b64encode(b'{}').decode()}})
            return 'synthetic-token' if 'token' in args else ''
        class Response(io.StringIO):
            pass
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'kubeconfig').write_text('clusters:\n- cluster:\n    server: https://cluster.example.test\n    certificate-authority-data: Y2E=\n')
            core = self.core()
            core.WORK = work; core.NS = 'ma-system'; core.run = run; core.apply = apply; core.save = Mock()
            core.secret = lambda name, *args: events.append('pull-secret') if name == 'ma-lifecycle-runtime-registry' else None
            config = {key: 'synthetic' for key in ('region','object_storage_namespace','artifact_bucket','runtime_artifact_access_key','runtime_artifact_secret_key','artifact_access_key','artifact_secret_key')}
            image = 'registry.example.test/runtime@sha256:' + 'b' * 64
            images = {name: {'pinned': image} for name in ('examples','identity','delivery')}
            with patch.object(runtime, 'core', core), patch.object(workload_security, 'harden_rbac'), patch.object(workload_security, 'install', side_effect=lambda *args: events.append('policy')), patch.object(workload_security, 'verify_manager_identity'), patch.object(runtime.ssl, 'create_default_context'), patch.object(runtime.urllib.request, 'urlopen', side_effect=lambda *args, **kwargs: Response('{"status":{"allowed":false}}')):
                runtime.install(config, images)
        self.assertEqual(events.count('renewal'), 2)
        self.assertLess(events.index('pull-secret'), events.index('policy'))
        self.assertLess(events.index('policy'), events.index('renewal'))


if __name__ == '__main__':
    unittest.main()
