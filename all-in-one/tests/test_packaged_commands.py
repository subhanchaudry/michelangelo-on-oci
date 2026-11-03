"""Exercise the distributed entry points, rather than private workspace copies."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('customer_package', ROOT/'scripts/package-stack.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackagedCommandsTests(unittest.TestCase):
    def test_all_profiles_are_reproducible_and_manifest_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            for profile in ('all-in-one', 'terraform', 'resource-manager'):
                first, second = (Path(directory)/(profile+suffix+'.zip') for suffix in ('-a', '-b'))
                package.package(first, profile=profile)
                package.package(second, profile=profile)
                self.assertEqual(first.read_bytes(), second.read_bytes())
                self.assertEqual(first.with_suffix('.zip.sha256').read_text().split()[0],
                                 hashlib.sha256(first.read_bytes()).hexdigest())
                with zipfile.ZipFile(first) as archive:
                    manifest = json.loads(archive.read('SOURCE-MANIFEST.json'))['files']
                    self.assertEqual(set(archive.namelist()), set(manifest)|{'SOURCE-MANIFEST.json'})
                    for name, digest in manifest.items():
                        self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)

    def test_extracted_acceptance_and_release_gate_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'site.zip'
            package.package(archive)
            site = root/'extracted'
            with zipfile.ZipFile(archive) as stream:
                stream.extractall(site)
            images = root/'images.json'
            digest = 'sha256:'+'a'*64
            images.write_text(json.dumps({str(i): {'runtime_digest': digest, 'digest': digest} for i in range(9)}))
            record = root/'acceptance.json'
            common = ['--cluster-uid', 'test-cluster', '--source-manifest', str(site/'SOURCE-MANIFEST.json'), '--images', str(images)]
            def run(script, mode, *args):
                return subprocess.run([sys.executable, '-B', str(site/'scripts'/script), mode, *args, *common],
                                      capture_output=True, text=True)
            result = run('site-acceptance.py', 'init', '--record', str(record))
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run('site-acceptance.py', 'check', '--record', str(record))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)['missing_checks']), 172)
            gate = root/'gate.json'
            result = run('release-gate.py', 'init', '--record', str(gate), '--acceptance', str(record))
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run('release-gate.py', 'check', '--record', str(gate), '--acceptance', str(record))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(json.loads(result.stdout)['cutover_allowed'])
            self.assertFalse(json.loads(result.stdout)['production_ready'])
            public = ['--profile', 'public-release', '--source-revision', 'a'*40, '--package', str(archive)]
            public_record = root/'public-acceptance.json'
            result = run('site-acceptance.py', 'init', '--record', str(public_record), *public)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run('site-acceptance.py', 'check', '--record', str(public_record), *public)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)['missing_checks']), 168)
            self.assertEqual(len(json.loads(result.stdout)['excluded_checks']), 4)
            public_gate = root/'public-gate.json'
            result = run('release-gate.py', 'init', '--record', str(public_gate), '--acceptance', str(public_record), *public)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run('release-gate.py', 'check', '--record', str(public_gate), '--acceptance', str(public_record), *public)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(json.loads(result.stdout)['release_eligible'])
            self.assertFalse(json.loads(result.stdout)['cutover_allowed'])


if __name__ == '__main__':
    unittest.main()
