"""Regression: mounted service credentials rotate without mutating caller headers."""
import ast
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import service_auth


class SDKRotationTest(unittest.TestCase):
    def test_connection_policy_is_bounded_idempotent_and_never_replays_calls(self):
        source_root = Path(os.environ.get('MA_REVIEW_SOURCE_DIR', str(Path(__file__).parents[1] / '.runtime/source')))
        reviewed = source_root / 'python/michelangelo/api/v2/services/base.py'
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            target = source / 'python/michelangelo/api/v2/services/base.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(reviewed.read_bytes())
            shutil.copytree(reviewed.parent / 'gen', target.parent / 'gen')
            service_auth.prepare_source(source)
            first = target.read_text()
            service_auth.prepare_source(source)
            self.assertEqual(first, target.read_text())
            tree = ast.parse(first)
            nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id in
                             ('_TIMEOUT_SECONDS', '_DEFAULT_SERVICE_CONFIG') for t in node.targets)]
            namespace = {}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(target), 'exec'), namespace)
            self.assertEqual(namespace['_DEFAULT_SERVICE_CONFIG'], {'methodConfig': [
                {'name': [{}], 'timeout': '60s', 'waitForReady': True}]})
            target.write_text(first.replace('"waitForReady": True', '"waitForReady": False'))
            with self.assertRaisesRegex(RuntimeError, 'transport policy'):
                service_auth.prepare_source(source)

    def test_reused_headers_rotation_and_explicit_credential(self):
        # Exercise the real reviewed SDK method after the portable source overlay.
        source_root = Path(os.environ.get('MA_REVIEW_SOURCE_DIR', str(Path(__file__).parents[1] / '.runtime/source')))
        reviewed = source_root / 'python/michelangelo/api/v2/services/base.py'
        if not reviewed.exists():
            self.fail('Set MA_REVIEW_SOURCE_DIR to the assembled source before running this regression')
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            target = source / 'python/michelangelo/api/v2/services/base.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(reviewed.read_bytes())
            shutil.copytree(reviewed.parent / 'gen', target.parent / 'gen')
            service_auth.prepare_source(source)
            service_auth.prepare_source(source)
            tree = ast.parse(target.read_text())
            provider = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DefaultHeaderProvider')
            method = next(n for n in provider.body if isinstance(n, ast.FunctionDef) and n.name == 'get_headers')
            namespace = {'os': os, '_HEADER_RPC_ENCODING': 'rpc-encoding',
                         '_HEADER_RPC_SERVICE': 'rpc-service', '_HEADER_RPC_CALLER': 'rpc-caller'}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(target), 'exec'), namespace)
            instance = type('Provider', (), {'service': 'ma-apiserver', 'caller': 'test-service'})()
            token_file = source / 'token'
            request_headers = {'x-request-id': 'same-reused-dictionary'}
            with patch.dict(os.environ, {'MA_API_TOKEN_FILE': str(token_file)}):
                token_file.write_text('token-A')
                first = namespace['get_headers'](instance, request_headers)
                token_file.write_text('token-B')
                second = namespace['get_headers'](instance, request_headers)
                self.assertEqual(first['authorization'], 'Bearer token-A')
                self.assertEqual(second['authorization'], 'Bearer token-B')
                self.assertEqual(request_headers, {'x-request-id': 'same-reused-dictionary'})
                explicit = {'authorization': 'Bearer explicit-user-token'}
                self.assertEqual(namespace['get_headers'](instance, explicit)['authorization'], explicit['authorization'])
                self.assertEqual(explicit, {'authorization': 'Bearer explicit-user-token'})
                token_file.unlink()
                self.assertNotIn('authorization', namespace['get_headers'](instance, request_headers))


if __name__ == '__main__':
    unittest.main()
