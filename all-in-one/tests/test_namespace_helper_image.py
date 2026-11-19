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
    def test_upstream_untagged_helper_preserves_command_on_inventory_backed_image(self):
        for image in ('busybox', 'busybox:latest', 'docker.io/library/busybox'):
            job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': 'michelangelo-temporal-namespace-setup'},
                   'spec': {'template': {'spec': {'initContainers': [{'name': 'wait-for-temporal-frontend', 'image': image, 'command': ['sh', '-c', 'until nc -z temporal 7233; do sleep 2; done']}],
                    'containers': [{'name': 'register-namespace', 'image': 'example.invalid/admin@sha256:' + 'a' * 64,
                     'command': ['sh', '-c', 'tctl --address temporal:7233 --namespace default namespace describe']} ]}}}}
            original = copy.deepcopy(job)
            with self.subTest(image=image), patch.object(render.mysql_router, 'transform', side_effect=lambda text, env: text):
                result = yaml.safe_load(render.render(yaml.safe_dump(job)))
            helper = result['spec']['template']['spec']['initContainers'][0]
            self.assertEqual(helper['image'], 'docker.io/library/alpine@sha256:0f49f331d9212aff11b932863be4eb01cdf962969aa86dab7d1ee21ac6f5f52b')
            self.assertEqual(helper['command'], original['spec']['template']['spec']['initContainers'][0]['command'])
            self.assertEqual(result['spec']['template']['spec']['containers'][0]['image'], original['spec']['template']['spec']['containers'][0]['image'])
