import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer/transport'))
spec = importlib.util.spec_from_file_location('namespace_transport_render', ROOT / 'installer/transport/render.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


class NamespaceHelperImageTests(unittest.TestCase):
    def test_upstream_untagged_helper_is_bound_to_qualified_binary(self):
        for image in ('busybox', 'busybox:latest', 'docker.io/library/busybox'):
            job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': 'michelangelo-temporal-namespace-setup'},
                   'spec': {'template': {'spec': {'initContainers': [{'name': 'wait-for-temporal-frontend', 'image': image, 'command': ['sh', '-c', 'until nc -z temporal 7233; do sleep 2; done']}],
                    'containers': [{'name': 'register-namespace', 'image': 'example.invalid/admin@sha256:' + 'a' * 64,
                     'command': ['sh', '-c', 'tctl --address temporal:7233 --namespace default namespace describe']} ]}}}}
            original = copy.deepcopy(job)
            with self.subTest(image=image), patch.object(render.mysql_router, 'transform', side_effect=lambda text, env: text):
                result = yaml.safe_load(render.render(yaml.safe_dump(job)))
            helper = result['spec']['template']['spec']['initContainers'][0]
            self.assertEqual(helper['image'], 'docker.io/library/busybox@sha256:f97baa533a26513c453a362be331a43eb60214302f449c16571b23cea141dce4')
            self.assertEqual(helper['command'], original['spec']['template']['spec']['initContainers'][0]['command'])
            self.assertEqual(result['spec']['template']['spec']['containers'][0]['image'], original['spec']['template']['spec']['containers'][0]['image'])
