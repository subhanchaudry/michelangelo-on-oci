import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class WorkflowCompilerCacheTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'seed_cache_test', Path(__file__).resolve().parents[1] / 'installer/seed.py')
        self.seed = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'install': types.ModuleType('install')}):
            spec.loader.exec_module(self.seed)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.paths = [root / name for name in ('inputs.json', 'package.py', 'seed.py')]
        for path, content in zip(self.paths, (b'{"task_image":"same-image"}', b'old environment', b'old seed')):
            path.write_bytes(content)

    def revision(self):
        return self.seed.lifecycle_compiler_revision(*self.paths)

    def test_same_image_recompiles_when_endpoint_or_compiler_changes(self):
        original = self.revision()
        self.assertEqual(original, self.revision())
        for path in self.paths:
            before = path.read_bytes()
            with self.subTest(changed=path.name):
                path.write_bytes(before + b' changed')
                self.assertNotEqual(original, self.revision())
                path.write_bytes(before)
                self.assertEqual(original, self.revision())

    def test_file_boundaries_cannot_alias(self):
        self.paths[0].write_bytes(b'ab')
        self.paths[1].write_bytes(b'c')
        first = self.revision()
        self.paths[0].write_bytes(b'a')
        self.paths[1].write_bytes(b'bc')
        self.assertNotEqual(first, self.revision())

    def test_missing_compiler_fails_before_reusing_cached_output(self):
        self.paths[1].unlink()
        with self.assertRaises(FileNotFoundError):
            self.revision()


if __name__ == '__main__':
    unittest.main()
