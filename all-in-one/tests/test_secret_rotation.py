"""External Secret rotation must change only consuming pod templates."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer/transport'))
spec = importlib.util.spec_from_file_location('rotation_render', ROOT / 'installer/transport/render.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

class SecretRotation(unittest.TestCase):
    def render(self, documents, revisions):
        with patch.dict(os.environ, {'MA_RUNTIME_SECRET_REVISIONS': json.dumps(revisions)}), patch.object(render.mysql_router, 'transform', side_effect=lambda text, env: text):
            return list(yaml.safe_load_all(render.render(yaml.safe_dump_all(documents))))

    def test_consumers_roll_on_rotation_and_remain_stable_on_retry(self):
        forms = [
            {'env': [{'name': 'MYSQL_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'database', 'key': 'password'}}}]},
            {'envFrom': [{'secretRef': {'name': 'database'}}]},
            {'volumes': [{'name': 'ca', 'secret': {'secretName': 'database'}}]},
        ]
        for form in forms:
            pod = {'containers': [{'name': 'app', 'image': 'example.com/app:1', **{k: v for k, v in form.items() if k != 'volumes'}}]}
            if 'volumes' in form:
                pod['volumes'] = form['volumes']
            deployment = {'kind': 'Deployment', 'metadata': {'name': 'app'}, 'spec': {'template': {'metadata': {'annotations': {'existing': 'preserved'}}, 'spec': pod}}}
            with self.subTest(form=form):
                before = self.render([deployment], {'database': 'a' * 64})
                after = self.render([deployment], {'database': 'b' * 64})
                self.assertNotEqual(before[0]['spec']['template'], after[0]['spec']['template'])
                self.assertEqual(after, self.render([deployment], {'database': 'b' * 64}))
                self.assertEqual(after, self.render([deployment], {'database': 'b' * 64, 'unrelated': 'c' * 64}))
                self.assertEqual(after[0]['spec']['template']['metadata']['annotations']['existing'], 'preserved')
                self.assertEqual(before[0]['spec']['template']['spec'], after[0]['spec']['template']['spec'])

    def test_unrelated_workloads_and_completed_jobs_are_unchanged(self):
        for kind in ('Deployment', 'Job'):
            doc = {'kind': kind, 'spec': {'template': {'spec': {'containers': [{'name': 'app', 'image': 'example.com/app:1'}]}}}}
            self.assertEqual(self.render([doc], {'database': 'a' * 64}), [doc])

    def test_invalid_revision_rejects_whole_render(self):
        for value in ({'database': 'raw-password'}, [], {'database': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.render([], value)

if __name__ == '__main__':
    unittest.main()
