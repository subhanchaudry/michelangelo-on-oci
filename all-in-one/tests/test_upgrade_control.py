import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('upgrade_control',Path(__file__).resolve().parents[1]/'installer/upgrade_control.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class UpgradeControlTests(unittest.TestCase):
 def core(self,directory):
  self.applied=[]
  return types.SimpleNamespace(WORK=Path(directory),NS='ma-system',APP='michelangelo',apply=self.applied.append,save=lambda *a:None)
 def test_cluster_failure_is_not_misread_as_fresh_install(self):
  with tempfile.TemporaryDirectory() as directory,patch.object(mod.subprocess,'run',return_value=types.SimpleNamespace(returncode=1,stdout='',stderr='connection refused')):
   with self.assertRaisesRegex(RuntimeError,'prior release history'):mod.begin(self.core(directory))
   self.assertEqual(self.applied[0]['data']['maintenance'],'true')
 def test_interrupted_release_requires_inspection(self):
  with tempfile.TemporaryDirectory() as directory,patch.object(mod.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='[{"revision":2,"status":"failed"}]',stderr='')):
   with self.assertRaisesRegex(RuntimeError,'no deployed revision'):mod.begin(self.core(directory))
 def test_fresh_release_has_no_false_rollback_checkpoint(self):
  with tempfile.TemporaryDirectory() as directory,patch.object(mod.subprocess,'run',return_value=types.SimpleNamespace(returncode=1,stdout='',stderr='Error: release: not found')):
   result=mod.begin(self.core(directory));self.assertTrue(result.is_dir())
 def test_terminal_states_exclude_running_and_unknown(self):
  for state in ['PIPELINE_RUN_STATE_SUCCEEDED','PIPELINE_RUN_STATE_FAILED','PIPELINE_RUN_STATE_KILLED',3,4,5,6]:self.assertTrue(mod.terminal({'status':{'state':state}}))
  for run in [{},{'status':{'state':'PIPELINE_RUN_STATE_RUNNING'}},{'status':{'state':0}}]:self.assertFalse(mod.terminal(run))

 def test_interrupted_upgrade_reuses_original_compatible_checkpoint(self):
  with tempfile.TemporaryDirectory() as directory:
   core=self.core(directory);checkpoint=core.WORK/'upgrade-checkpoints'/'original';checkpoint.mkdir(parents=True)
   (checkpoint/'checkpoint.json').write_text(json.dumps({'helm_revision':7,'ready':True,'resources':[{'image':'original'}]}))
   (core.WORK/'active-upgrade-checkpoint.json').write_text(json.dumps({'directory':str(checkpoint)}))
   with patch.object(mod,'native_gate'),patch.object(mod,'verify_native_gate'),patch.object(mod,'drain') as drain,patch.object(mod.subprocess,'run') as history:
    self.assertEqual(mod.begin(core),checkpoint.resolve());history.assert_not_called();drain.assert_called_once_with(core)
   self.assertEqual(json.loads((checkpoint/'checkpoint.json').read_text())['resources'],[{'image':'original'}])
   mod.complete(core);self.assertFalse((core.WORK/'active-upgrade-checkpoint.json').exists())
 def test_compute_drain_includes_unknown_and_running_jobs(self):
  ray={'status':{'state':'RAY_JOB_STATE_RUNNING'}};spark={'status':{'statusConditions':[{'type':'Succeeded','status':'CONDITION_STATUS_FALSE'}]}}
  def get(core,resource):return {'items':[ray] if resource.startswith('ray') else [spark]}
  with patch.object(mod,'get',side_effect=get):
   self.assertEqual(mod.active_compute(None),[ray])
   ray['status']['state']='RAY_JOB_STATE_SUCCEEDED';self.assertEqual(mod.active_compute(None),[])
   spark['status']['statusConditions']=[];self.assertEqual(mod.active_compute(None),[spark])

 def test_precontainment_rollback_is_refused_with_maintenance_retained(self):
  with tempfile.TemporaryDirectory() as directory:
   core=self.core(directory)
   checkpoint=core.WORK/'old';checkpoint.mkdir()
   (checkpoint/'checkpoint.json').write_text(json.dumps({'helm_revision':1,'ready':True,'resources':[]}))
   with patch.object(mod,'native_gate'),patch.object(mod,'verify_native_gate'):
    with self.assertRaisesRegex(RuntimeError,'uncontained runtime'):mod.rollback(core,checkpoint)
   self.assertEqual(self.applied[0]['data']['maintenance'],'true')
