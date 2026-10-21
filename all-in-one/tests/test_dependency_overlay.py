import hashlib
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'installer'))
import dependency_overlay as overlay


class DependencyOverlayTests(unittest.TestCase):
    def test_platform_and_collector_use_patched_go_and_networking(self):
        for name in ('platform','collector'):
            module=(ROOT/'installer/dependencies'/name/'go.mod').read_text()
            self.assertIn('go 1.26.10',module)
            self.assertIn('golang.org/x/net v0.60.0',module)
            self.assertIn('github.com/klauspost/compress v1.18.7',module)

    def test_unknown_platform_module_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory);(source/'go').mkdir()
            module=source/'go/go.mod';module.write_text('module unreviewed')
            with self.assertRaisesRegex(RuntimeError,'reviewed dependency base'):overlay.prepare_source(source)
            self.assertEqual(module.read_text(),'module unreviewed')

    def test_runtime_locks_are_complete_hashed_wheel_inputs(self):
        for name in ('builder', 'delivery', 'identity', 'serving', 'cpu', 'certifi', 'ray-vendor'):
            lines = (ROOT/'installer/dependencies'/f'{name}-requirements.lock').read_text().splitlines()
            self.assertTrue(lines)
            for line in lines:
                self.assertRegex(line, r'^[-A-Za-z0-9_.]+ @ https://(?:files\.pythonhosted\.org|download(?:-r2)?\.pytorch\.org)/\S+\.whl --hash=sha256:[0-9a-f]{64}$')
        cpu = (ROOT/'installer/dependencies/cpu-requirements.lock').read_text().lower()
        self.assertNotIn('pyspark', cpu)
        for package in ('ray', 'torch', 'transformers', 'xgboost', 'pyarrow'):
            self.assertRegex(cpu, rf'(?m)^{package} @ ')
        self.assertEqual((ROOT/'installer/dependencies/identity-requirements.lock').read_bytes(),
                         (ROOT/'installer/identity-service/requirements.lock').read_bytes())

    def test_python_security_fixes_cover_global_and_vendored_copies(self):
        for name in ('cpu', 'serving', 'certifi'):
            lock = (ROOT/'installer/dependencies'/f'{name}-requirements.lock').read_text()
            self.assertRegex(lock, r'(?m)^certifi @ .*certifi-2026\.10\.19-py3-none-any\.whl ')
        for name in ('cpu', 'ray-vendor'):
            lock = (ROOT/'installer/dependencies'/f'{name}-requirements.lock').read_text()
            self.assertRegex(lock, r'(?m)^aiohttp @ .*aiohttp-3\.14\.4-cp312-cp312-')

    def test_ray_vendor_closure_matches_the_resolved_cpu_runtime(self):
        def entries(name):
            return {line.split(' @ ')[0].replace('-', '_'): line for line in
                    (ROOT/'installer/dependencies'/name).read_text().splitlines()}
        vendor = entries('ray-vendor-requirements.lock')
        cpu = entries('cpu-requirements.lock')
        self.assertEqual(set(vendor), {'aiohttp', 'aiohappyeyeballs', 'aiosignal',
                         'attrs', 'frozenlist', 'multidict', 'propcache', 'yarl',
                         'idna', 'typing_extensions'})
        self.assertEqual(vendor, {name: cpu[name] for name in vendor})

    def test_source_overlay_is_idempotent_and_rejects_unknown_base(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory); (source/'docker').mkdir()
            service = source/'docker/service.Dockerfile'
            service.write_text('FROM gcr.io/distroless/base-debian12:latest@sha256:fabbf1c0c357a3d42550111351daed089b20a2c954df13ee2fcff60602515e84\n')
            ui = source/'docker/ui-prebuilt.Dockerfile'
            ui.write_text('RUN apk add --no-cache --upgrade libexpat\n')
            overlay.prepare_source(source)
            first = [service.read_bytes(), ui.read_bytes()]
            overlay.prepare_source(source)
            self.assertEqual(first, [service.read_bytes(), ui.read_bytes()])
            service.write_text('FROM unknown:latest\n')
            with self.assertRaises(RuntimeError):
                overlay.prepare_source(source)

    def test_collector_refuses_unreviewed_source_before_creating_build_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'original'; (source/'historyserver').mkdir(parents=True)
            (source/'historyserver/go.mod').write_text('unexpected module')
            destination = Path(directory)/'build'
            for commit in ('different', overlay.COLLECTOR_COMMIT):
                with self.assertRaises(RuntimeError):
                    overlay.prepare_collector(source, destination, commit)
                self.assertFalse(destination.exists())

    def test_serving_cleanup_is_idempotent_and_keeps_nonroot_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory); (source/'docker').mkdir()
            dockerfile = source/'docker/support-runtime.Dockerfile'
            dockerfile.write_bytes((ROOT/'installer/release/platform/releases/v0.11.0/support-runtime.Dockerfile').read_bytes())
            overlay.prepare_demo(source)
            first = dockerfile.read_bytes()
            overlay.prepare_demo(source)
            self.assertEqual(first, dockerfile.read_bytes())
            serving = dockerfile.read_text().split('FROM ${TRITON_BASE} AS triton\n')[1].split('FROM ${PYTHON_BASE} AS delivery')[0]
            self.assertIn('libssl3t64=3.0.13-0ubuntu3.16', serving)
            self.assertIn('/usr/local/cuda-13.4/NsightSystems-cli-2026.5.1', serving)
            self.assertIn('python3 -m pip install --no-cache-dir --no-deps --require-hashes --upgrade -r /opt/runtime-patches/certifi-requirements.lock', serving)
            self.assertTrue(serving.rstrip().endswith('USER 65532:65532'))


if __name__ == '__main__':
    unittest.main()
