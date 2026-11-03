"""Public-release eligibility never implies migration or customer acceptance."""
import copy
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_gate', ROOT/'scripts/release-gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root/'SOURCE-MANIFEST.json'
        self.manifest.write_text(json.dumps({'profile':'all-in-one', 'files':{
            'fixture.txt':hashlib.sha256(b'synthetic fixture').hexdigest()}}))
        self.package = self.root/'package.zip'
        with zipfile.ZipFile(self.package, 'w') as archive:
            archive.write(self.manifest, 'SOURCE-MANIFEST.json')
            archive.writestr('fixture.txt', b'synthetic fixture')
        self.digest = 'sha256:'+'a'*64
        self.images = {str(i): {'digest':self.digest, 'runtime_digest':self.digest} for i in range(9)}
        self.image_file = self.root/'images.json'
        self.image_file.write_text(json.dumps(self.images))
        self.expected = gate.site.template('fixture-cluster', self.manifest, self.image_file,
                                          'public-release', 'a'*40, self.package)
        self.acceptance = copy.deepcopy(self.expected)
        self.record = gate.template(self.expected, self.images)
        self.passcheck = {'status':'passed', 'reviewer':'synthetic-unit-fixture',
                          'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'evidence':[self.file('fixture.json', {'synthetic':True})]}
        for checks in [self.acceptance['site_checks'], *self.acceptance['models'].values(),
                       self.record['checks'], self.record['training']]:
            for name in checks:
                checks[name] = copy.deepcopy(self.passcheck)
        self.scan = {'ArtifactType':'container_image',
                     'CreatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     'Metadata':{'RepoDigests':['fixture@'+self.digest]},
                     'Results':[{'Packages':[{'Name':'fixture', 'Version':'1'}]}]}
        self.record['security'] = {'status':'reviewed', 'reviewer':'synthetic-unit-fixture',
            'inventory_evidence':[self.file('inventory.json', {'images':[{
                'reference':'fixture@'+self.digest, 'resolved_executable_digest':self.digest}]})],
            'images':[{**copy.deepcopy(self.passcheck), 'name':'fixture',
                'reference':'fixture@'+self.digest, 'executable_digest':self.digest,
                'scan_evidence':[self.file('scan.json', self.scan)]}], 'findings':[]}

    def file(self, name, value):
        p = self.root/name
        p.write_text(json.dumps(value))
        return {'path':name, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

    def check(self):
        return gate.validate(self.record, self.acceptance, self.expected, self.root, self.root, self.images)

    def rescan(self):
        self.record['security']['images'][0]['scan_evidence'] = [self.file('scan.json', self.scan)]

    def test_complete_fixture_does_not_authorize_cutover(self):
        result = self.check()
        self.assertTrue(result['release_eligible'])
        self.assertFalse(result['cutover_allowed'])
        self.assertFalse(result['production_ready'])
        self.assertEqual(len(result['excluded_checks']), 4)
        self.assertTrue(all(c['status']=='excluded' for c in result['excluded_checks'].values()))

    def test_second_human_exclusion_cannot_be_relabelled_passed(self):
        next(iter(self.acceptance['excluded_checks'].values()))['status'] = 'passed'
        with self.assertRaises(ValueError):
            self.check()

    def test_altered_payload_or_extra_file_cannot_use_original_manifest(self):
        for files in ({'fixture.txt':b'changed'},
                      {'fixture.txt':b'synthetic fixture', 'unexpected.txt':b'extra'}):
            with self.subTest(files=list(files)):
                with zipfile.ZipFile(self.package, 'w') as archive:
                    archive.write(self.manifest, 'SOURCE-MANIFEST.json')
                    for name, content in files.items():
                        archive.writestr(name, content)
                with self.assertRaises(ValueError):
                    gate.site.template('fixture-cluster', self.manifest, self.image_file,
                                       'public-release', 'a'*40, self.package)

    def test_required_checks_cannot_be_removed(self):
        del self.acceptance['site_checks']['iam_admin_login']
        with self.assertRaises(ValueError):
            self.check()

    def test_public_profile_requires_application_restore_and_automated_authorization(self):
        for name in ('full_independent_site_restore', 'authorization_regressions', 'complete_executable_inventory'):
            with self.subTest(name=name):
                previous = self.record['checks'][name]
                self.record['checks'][name] = gate.pending()
                self.assertFalse(self.check()['release_eligible'])
                self.record['checks'][name] = previous

    def test_empty_or_stale_scan_is_not_clean(self):
        for field, value in [('Results', []), ('CreatedAt', '2000-01-01T00:00:00Z')]:
            with self.subTest(field=field):
                previous = self.scan[field]
                self.scan[field] = value
                self.rescan()
                self.assertFalse(self.check()['release_eligible'])
                self.scan[field] = previous

    def test_risk_exceptions_never_satisfy_public_release(self):
        self.scan['Results'][0]['Vulnerabilities'] = [{'VulnerabilityID':'CVE-FIXTURE', 'Severity':'HIGH'}]
        self.rescan()
        finding = {'id':'CVE-FIXTURE', 'severity':'HIGH', 'executable_digests':[self.digest],
            'analysis':'synthetic unit fixture', 'owner':'fixture', 'reviewer':'fixture',
            'exploitable':False, 'evidence':self.passcheck['evidence'], 'disposition':'approved-exception'}
        self.record['security']['findings'] = [finding]
        self.assertFalse(self.check()['release_eligible'])
        finding['disposition'] = 'fixed'
        self.assertFalse(self.check()['release_eligible'])
        finding['disposition'] = 'not-affected'
        self.assertTrue(self.check()['release_eligible'])
        finding['executable_digests'] = ['sha256:'+'b'*64]
        self.assertFalse(self.check()['release_eligible'])

    def test_package_source_and_cluster_bindings(self):
        for field in ('package_sha256', 'source_revision', 'cluster_uid', 'images_sha256'):
            with self.subTest(field=field):
                previous = self.record['bindings'][field]
                self.record['bindings'][field] = 'different'
                with self.assertRaises(ValueError):
                    self.check()
                self.record['bindings'][field] = previous

    def test_mismatched_package_manifest_rejected(self):
        self.manifest.write_text('changed')
        with self.assertRaises(ValueError):
            gate.site.template('fixture-cluster', self.manifest, self.image_file,
                               'public-release', 'a'*40, self.package)

    def test_upstream_resource_view_is_not_claimed(self):
        for checks in self.expected['models'].values():
            self.assertIn('studio_logs_outputs', checks)
            self.assertNotIn('studio_logs_outputs_resources', checks)
        self.assertTrue(self.expected['upstream_limitations'])


if __name__ == '__main__':
    unittest.main()
