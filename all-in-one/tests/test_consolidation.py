import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load('packager', 'package-stack.py')
acceptance = load('acceptance', 'site-acceptance.py')


class PackageTests(unittest.TestCase):
    def test_core_payload_and_pending_catalog(self):
        files = package.payload()
        nested = json.loads((ROOT/'installer/release/SOURCE-MANIFEST.json').read_text())
        for name, sha in nested.items():
            self.assertEqual(hashlib.sha256(files['installer/release/'+name]).hexdigest(), sha, name)
        self.assertIn('ACCEPTANCE-CONTRACT.json', files)
        self.assertNotIn('terraform.tfvars', files)
        self.assertNotIn('terraform.tfstate', files)

    def test_deterministic_complete_archive_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / 'a.zip', Path(temp) / 'b.zip'
            package.package(a)
            package.package(b)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with zipfile.ZipFile(a) as z:
                manifest = json.loads(z.read('SOURCE-MANIFEST.json'))['files']
                self.assertEqual(set(z.namelist()), set(manifest) | {'SOURCE-MANIFEST.json'})
                for name, sha in manifest.items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), sha)
            with self.assertRaises(ValueError):
                package.package(a)

    def test_unsafe_path_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'PACKAGE-FILES.json').write_text('["../outside"]')
            with self.assertRaises(ValueError):
                package.payload(root)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'PACKAGE-FILES.json').write_text('["linked"]')
            (root / 'linked').symlink_to(ROOT / 'README.md')
            with self.assertRaises(ValueError):
                package.payload(root)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / 'manifest.json'
        self.images = self.root / 'images.json'
        self.manifest.write_text('{"test": "source"}')
        self.images.write_text('{"test": "image"}')
        self.expected = acceptance.template('test-cluster', self.manifest, self.images)

    def test_all_pilot_checks_remain_pending(self):
        self.assertEqual(len(self.expected['models']), 8)
        self.assertTrue(all(len(checks) == 20 for checks in self.expected['models'].values()))
        report = acceptance.validate(self.expected, self.expected, self.root)
        self.assertEqual(len(report['missing_checks']), 172)
        self.assertFalse(report['production_ready'])

    def test_other_site_or_build_rejected(self):
        for field in ('cluster_uid', 'source_manifest_sha256', 'images_sha256'):
            record = copy.deepcopy(self.expected)
            record[field] = 'different'
            with self.assertRaises(ValueError):
                acceptance.validate(record, self.expected, self.root)

    def passed_fixture(self):
        # Unit fixture only; never distributed as acceptance evidence.
        evidence = self.root / 'fixture.txt'
        evidence.write_text('unit fixture')
        record = copy.deepcopy(self.expected)
        for group in [record['site_checks'], *record['models'].values()]:
            for name in group:
                group[name] = {'status': 'passed', 'reviewer': 'test fixture',
                               'completed_at': '2026-01-01T00:00:00+00:00',
                               'evidence': [{'path': evidence.name, 'sha256': acceptance.digest(evidence)}]}
        return record, evidence

    def test_reviewed_record_and_changed_evidence(self):
        record, evidence = self.passed_fixture()
        self.assertEqual(acceptance.validate(record, self.expected, self.root)['missing_checks'], [])
        evidence.write_text('changed')
        with self.assertRaises(ValueError):
            acceptance.validate(record, self.expected, self.root)

    def test_evidence_escape_rejected(self):
        record, _ = self.passed_fixture()
        first = next(iter(record['site_checks'].values()))
        first['evidence'][0]['path'] = '../elsewhere'
        with self.assertRaises(ValueError):
            acceptance.validate(record, self.expected, self.root)


if __name__ == '__main__':
    unittest.main()
