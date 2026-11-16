import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
import runtime


class ServingProbePermissionsTests(unittest.TestCase):
    def permissions(self):
        return json.loads((ROOT / 'installer/release/platform/releases/v0.11.0/serving-permissions.example.json').read_text())['items']

    def test_only_catalog_serving_roles_receive_post_proxy_permission(self):
        permissions = self.permissions()
        original = copy.deepcopy(permissions)
        runtime.configure_serving_probes(permissions)
        projects = {m['project'] for m in json.loads((ROOT / 'installer/release/platform/serving/lifecycle/catalog.json').read_text())['models']}
        changed = []
        for before, after in zip(original, permissions):
            if before != after:
                self.assertEqual(after['kind'], 'Role')
                self.assertEqual(after['metadata']['name'], 'michelangelo-serving-manager')
                self.assertIn(after['metadata']['namespace'], projects)
                self.assertEqual(after['rules'][:-1], before['rules'])
                self.assertEqual(after['rules'][-1], {'apiGroups': [''], 'resources': ['pods/proxy'], 'verbs': ['create']})
                changed.append(after['metadata']['namespace'])
        self.assertCountEqual(changed, projects)
        once = copy.deepcopy(permissions)
        runtime.configure_serving_probes(permissions)
        self.assertEqual(permissions, once)

    def test_unreviewed_and_missing_namespaces_are_rejected(self):
        for namespace in ('default', 'ma-system', 'unreviewed-project'):
            permissions = self.permissions()
            role = next(p for p in permissions if p.get('kind') == 'Role' and p['metadata']['name'] == 'michelangelo-serving-manager')
            role['metadata']['namespace'] = namespace
            with self.subTest(namespace=namespace), self.assertRaises(RuntimeError):
                runtime.configure_serving_probes(permissions)
        permissions = [p for p in self.permissions() if not (p.get('kind') == 'Role' and p['metadata']['name'] == 'michelangelo-serving-manager')]
        with self.assertRaises(RuntimeError):
            runtime.configure_serving_probes(permissions)
