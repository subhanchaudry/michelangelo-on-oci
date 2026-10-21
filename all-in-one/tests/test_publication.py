import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('publication',ROOT/'installer/qualification/publication.py')
pub=importlib.util.module_from_spec(spec);spec.loader.exec_module(pub)
P=pub.PREFIX

class PublicationTests(unittest.TestCase):
 def setUp(self):
  self.blobs={}
  def blob(name,value):
   data=value if isinstance(value,bytes) else json.dumps(value).encode();uri='s3://site/studio-demos/lifecycle/project/'+name;self.blobs[uri]=data;return uri,hashlib.sha256(data).hexdigest()
  package,package_sha=blob('model.tar',b'model-package');dataset,dataset_sha=blob('dataset.json',{'demo':'test'});evaluation,evaluation_sha=blob('evaluation.json',{'demo':'test','quality_gate':{'passed':True}})
  registration={'model_registered':True,'model_revision':'candidate'}
  log,log_sha=blob('task.log',json.dumps({'event':'completed','result':{'lifecycle':registration}}).encode())
  self.run={'metadata':{'name':'training','namespace':'project'},'status':{'state':'PIPELINE_RUN_STATE_SUCCEEDED','steps':[{'subSteps':[{'output':{'lifecycle':registration,'source_run':'training','log_uri':log,'log_sha256':log_sha}}]}]}}
  self.model={'metadata':{'name':'candidate','namespace':'project','uid':'native-candidate-1','annotations':{P+'demo':'test',P+'publication-state':'pending',P+'package-sha256':package_sha,P+'dataset-uri':dataset,P+'dataset-sha256':dataset_sha,P+'evaluation-uri':evaluation,P+'evaluation-sha256':evaluation_sha},'labels':{P+'eligible':'false'}},'spec':{'sourcePipelineRun':{'name':'training','namespace':'project'},'performanceEvaluationReport':{'name':'evaluation','namespace':'project'},'deployableArtifactUri':[package]}}
  self.report={'metadata':{'name':'evaluation','namespace':'project'},'spec':{'sealed':True,'sourcePipelineRun':{'name':'training','namespace':'project'}}};self.updates=[]
  self.model['metadata']['annotations'].update({P+'training-dataset-uri':dataset,P+'training-dataset-sha256':dataset_sha})
  registration['candidate_binding']=copy.deepcopy(pub.candidate_binding(self.model))
  self.blobs[log]=json.dumps({'event':'completed','result':{'lifecycle':registration}}).encode()
  self.run['status']['steps'][0]['subSteps'][0]['output']['log_sha256']=hashlib.sha256(self.blobs[log]).hexdigest()
 def call(self,kind,action,body):
  if kind=='PipelineRun':return {'pipelineRun':self.run}
  if kind=='EvaluationReport':return {'evaluationReport':self.report}
  self.updates.append(copy.deepcopy(body));return {'model':body['model']}
 def read(self,uri,limit):return self.blobs[uri]
 def reconcile(self):return pub.reconcile(self.model,self.call,lambda ns:self.read)
 def test_success_and_idempotent_retry(self):
  self.assertEqual(self.reconcile(),'eligible');self.assertEqual(self.reconcile(),'eligible');self.assertEqual(len(self.updates),1);self.assertEqual(self.model['metadata']['labels'][P+'eligible'],'true')
 def test_running_never_publishes(self):
  self.run['status']['state']='PIPELINE_RUN_STATE_RUNNING';self.assertEqual(self.reconcile(),'pending');self.assertFalse(self.updates)
 def test_failures_and_cancellation_quarantine(self):
  for state in ['PIPELINE_RUN_STATE_FAILED','PIPELINE_RUN_STATE_KILLED','PIPELINE_RUN_STATE_SKIPPED']:
   with self.subTest(state=state):
    self.setUp();self.run['status']['state']=state;self.assertEqual(self.reconcile(),'quarantined');self.assertEqual(self.model['metadata']['labels'][P+'eligible'],'false')
 def test_output_persistence_failure(self):
  self.run['status']['steps']=[];self.assertEqual(self.reconcile(),'quarantined')
 def test_corrupt_artifact(self):
  self.blobs[self.model['spec']['deployableArtifactUri'][0]]=b'corrupt';self.assertEqual(self.reconcile(),'quarantined')
 def test_wrong_project(self):
  self.run['metadata']['namespace']='other';self.assertEqual(self.reconcile(),'quarantined')
 def test_missing_log_checksum(self):
  self.run['status']['steps'][0]['subSteps'][0]['output'].pop('log_sha256');self.assertEqual(self.reconcile(),'quarantined')
 def test_unsealed_evaluation(self):
  self.report['spec']['sealed']=False;self.assertEqual(self.reconcile(),'quarantined')
 def test_training_provenance_checksum_required(self):
  self.model['metadata']['annotations'][P+'training-provenance']=json.dumps({'uri':'s3://site/provenance','sha256':'invalid'})
  self.assertEqual(self.reconcile(),'quarantined')
 def test_recreated_candidate_cannot_reuse_successful_output(self):
  self.model['metadata']['uid']='recreated-candidate-2'
  self.assertEqual(self.reconcile(),'quarantined')
  self.assertIn('binding-mismatch',self.model['metadata']['annotations'][P+'publication-reason'])
 def test_substituted_valid_package_cannot_reuse_successful_output(self):
  uri='s3://site/studio-demos/lifecycle/project/other.tar';data=b'other valid model';self.blobs[uri]=data
  self.model['spec']['deployableArtifactUri']=[uri]
  self.model['metadata']['annotations'][P+'package-sha256']=hashlib.sha256(data).hexdigest()
  self.assertEqual(self.reconcile(),'quarantined')
 def test_log_must_bind_exact_candidate(self):
  output=self.run['status']['steps'][0]['subSteps'][0]['output']
  data=json.dumps({'event':'completed','result':{'lifecycle':{'model_registered':True,'model_revision':'candidate'}}}).encode()
  self.blobs[output['log_uri']]=data;output['log_sha256']=hashlib.sha256(data).hexdigest()
  self.assertEqual(self.reconcile(),'quarantined')
 def test_network_failure_is_retryable_and_never_eligible(self):
  def fail(uri,limit):raise TimeoutError()
  with self.assertRaises(TimeoutError):pub.reconcile(self.model,self.call,lambda ns:fail)
  self.assertFalse(self.updates);self.assertEqual(self.model['metadata']['labels'][P+'eligible'],'false')
 def test_pretty_printed_diagnostics_before_final_completion_are_valid(self):
  output=self.run['status']['steps'][0]['subSteps'][0]['output'];uri=output['log_uri']
  self.blobs[uri]=b'{\n  "training_metric": 0.25\n}\n'+self.blobs[uri]+b'\n'
  output['log_sha256']=hashlib.sha256(self.blobs[uri]).hexdigest()
  self.assertEqual(self.reconcile(),'eligible')
 def test_earlier_completion_cannot_mask_invalid_final_record(self):
  output=self.run['status']['steps'][0]['subSteps'][0]['output'];uri=output['log_uri']
  self.blobs[uri]+=b'\n{unfinished completion'
  output['log_sha256']=hashlib.sha256(self.blobs[uri]).hexdigest()
  self.assertEqual(self.reconcile(),'quarantined')
  self.assertEqual(self.model['metadata']['annotations'][P+'publication-reason'],'persisted-completion-log-invalid')
 def test_conflict_does_not_mutate_server_record(self):
  def conflict(kind,action,body):
   if action=='Update':raise RuntimeError('conflict')
   return self.call(kind,action,body)
  with self.assertRaises(RuntimeError):pub.reconcile(copy.deepcopy(self.model),conflict,lambda ns:self.read)
  self.assertEqual(self.model['metadata']['annotations'][P+'publication-state'],'pending');self.assertEqual(self.reconcile(),'eligible')

if __name__=='__main__':unittest.main()
