import copy
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('release_gate',Path(__file__).resolve().parents[1]/'scripts/release-gate.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

class ReleaseGateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.digest='sha256:'+'a'*64
  self.images={str(i):{'runtime_digest':self.digest,'digest':self.digest} for i in range(9)}
  self.expected={'schema_version':1,'cluster_uid':'test-cluster','source_manifest_sha256':'source','images_sha256':'images','site_checks':{'second_user':{}},'models':{'bert':{'train':{}}}}
  self.acceptance=copy.deepcopy(self.expected)
  self.record=gate.template(self.expected,self.images)
  self.passcheck={'status':'passed','reviewer':'unit-test-fixture','completed_at':'2026-01-01T00:00:00+00:00','evidence':[self.file('check.json',{'synthetic_test_fixture':True})]}
  self.acceptance['site_checks']['second_user']=copy.deepcopy(self.passcheck);self.acceptance['models']['bert']['train']=copy.deepcopy(self.passcheck)
  for group in ('checks','training'):
   self.record[group]={name:copy.deepcopy(self.passcheck) for name in self.record[group]}
  self.scan={'ArtifactType':'container_image','Metadata':{'RepoDigests':['test@'+self.digest]},'Results':[]}
  self.record['security']={'status':'reviewed','reviewer':'fixture','inventory_evidence':[self.file('inventory.json',{'images':[{'reference':'test@'+self.digest,'resolved_executable_digest':self.digest}]})],
   'images':[{**copy.deepcopy(self.passcheck),'name':'fixture','reference':'test@'+self.digest,'executable_digest':self.digest,'scan_evidence':[self.file('scan.json',self.scan)]}],'findings':[]}
 def file(self,name,data):
  path=self.root/name;path.write_text(json.dumps(data));return {'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 def check(self):return gate.validate(self.record,self.acceptance,self.expected,self.root,self.root,self.images)
 def test_complete_synthetic_fixture(self):self.assertTrue(self.check()['cutover_allowed'])
 def test_second_user_pending_blocks(self):
  self.acceptance['site_checks']['second_user']['status']='not-run';self.assertFalse(self.check()['cutover_allowed'])
 def test_bert_training_pending_blocks(self):
  self.record['training']['bert-cola-finetuning']['status']='not-run';self.assertFalse(self.check()['cutover_allowed'])
 def test_cross_build_evidence_rejected(self):
  self.record['bindings']['cluster_uid']='other'
  with self.assertRaises(ValueError):self.check()
 def test_unscanned_supporting_component_blocks(self):
  self.record['security']['inventory_evidence']=[self.file('inventory.json',{'images':[{'reference':'unscanned:tag'}]})]
  self.assertFalse(self.check()['cutover_allowed'])
 def test_detection_cannot_be_omitted_or_marked_fixed(self):
  self.scan['Results']=[{'Vulnerabilities':[{'VulnerabilityID':'CVE-FIXTURE','Severity':'HIGH'}]}]
  self.record['security']['images'][0]['scan_evidence']=[self.file('scan.json',self.scan)]
  self.assertFalse(self.check()['cutover_allowed'])
  self.record['security']['findings']=[{'id':'CVE-FIXTURE','severity':'HIGH','executable_digests':[self.digest],'disposition':'fixed','analysis':'fixture','owner':'fixture','evidence':self.passcheck['evidence']}]
  self.assertFalse(self.check()['cutover_allowed'])
 def test_changed_evidence_blocks(self):
  (self.root/'check.json').write_text('changed')
  with self.assertRaises(ValueError):self.check()
 def test_remaining_not_affected_still_requires_explicit_expiring_approval(self):
  self.scan['Results']=[{'Vulnerabilities':[{'VulnerabilityID':'CVE-FIXTURE','Severity':'HIGH'}]}]
  self.record['security']['images'][0]['scan_evidence']=[self.file('scan.json',self.scan)]
  finding={'id':'CVE-FIXTURE','severity':'HIGH','executable_digests':[self.digest],'disposition':'not-affected','exploitable':False,'analysis':'fixture','owner':'fixture','evidence':self.passcheck['evidence']}
  self.record['security']['findings']=[finding];self.assertFalse(self.check()['cutover_allowed'])
  finding.update(compensating_controls=['fixture'],expires_at=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat(),approval={'approver':'fixture','explicit':True,'reference':'unit fixture only','evidence':self.passcheck['evidence']})
  self.assertTrue(self.check()['cutover_allowed'])
 def test_automatic_retirement_refused(self):
  self.record['cutover']['delete_old_pilot']=True;self.assertFalse(self.check()['cutover_allowed'])
 def test_missing_executable_manifests_refused(self):
  with self.assertRaises(ValueError):gate.template(self.expected,{'old-index':self.digest})

 def test_expired_exception_blocks(self):
  self.scan['Results']=[{'Vulnerabilities':[{'VulnerabilityID':'CVE-FIXTURE','Severity':'HIGH'}]}]
  self.record['security']['images'][0]['scan_evidence']=[self.file('scan.json',self.scan)]
  self.record['security']['findings']=[{'id':'CVE-FIXTURE','severity':'HIGH','executable_digests':[self.digest],
   'disposition':'approved-exception','exploitable':False,'analysis':'fixture','owner':'fixture','evidence':self.passcheck['evidence'],
   'compensating_controls':['fixture'],'expires_at':'2000-01-01T00:00:00+00:00',
   'approval':{'approver':'fixture','explicit':True,'reference':'expired fixture','evidence':self.passcheck['evidence']}}]
  self.assertFalse(self.check()['cutover_allowed'])
