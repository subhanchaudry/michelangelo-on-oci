import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
import operator_images


class OperatorImagesTests(unittest.TestCase):
    def test_missing_image_fails_before_namespace_or_secret_changes(self):
        core = types.SimpleNamespace(apply=Mock(), secret=Mock())
        with self.assertRaises(KeyError):
            operator_images.prepare(core, {})
        core.apply.assert_not_called()
        core.secret.assert_not_called()

    def test_mutable_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            operator_images.image({'kuberay_image': 'registry.example/operator:latest'}, 'kuberay')

    def test_digest_survives_helm_repository_tag_join(self):
        ref = 'registry.example/namespace/operator:candidate@sha256:' + 'a' * 64
        result = operator_images.image({'kuberay_image': ref}, 'kuberay')
        self.assertEqual(result['registry'] + '/' + result['repository'] + ':' + result['tag'], ref)


if __name__ == '__main__':
    unittest.main()
