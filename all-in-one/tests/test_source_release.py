import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('source_release',Path(__file__).resolve().parents[1]/'installer/source_release.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class SourceReleaseTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.manifest=self.root/'manifest.json';self.patch=self.root/'compatibility.patch';self.patch.write_text('fixture')
  self.value={'upstream_tag':'v-test','upstream_commit':'a'*40,'repository':'fixture-repository','patch':{'file':self.patch.name,'sha256':hashlib.sha256(self.patch.read_bytes()).hexdigest()}}
  self.manifest.write_text(json.dumps(self.value));self.calls=[]
  def run(command,**kwargs):
   self.calls.append(command)
   if command[1]=='clone':Path(command[-1]).mkdir()
   if command[1]=='rev-parse':return self.value['upstream_commit']
   return ''
  self.package_manifest=self.root/'SOURCE-MANIFEST.json';self.package_manifest.write_text('{"fixture":1}')
  self.core=types.SimpleNamespace(WORK=self.root,ROOT=self.root/'installer',run=run,save=lambda p,v:p.write_text(json.dumps(v)))
  self.override=patch.object(mod,'SELECTED',self.manifest);self.override.start();self.addCleanup(self.override.stop)
 def test_reconstruction_and_retry_preserve_source(self):
  first=mod.assemble(self.core);(first/'retained').write_text('keep')
  second=mod.assemble(self.core);self.assertEqual(first,second);self.assertEqual((second/'retained').read_text(),'keep')
  self.assertEqual(sum(cmd[1]=='clone' for cmd in self.calls),1)
 def test_tampered_patch_refused_before_clone(self):
  self.patch.write_text('tampered')
  with self.assertRaisesRegex(RuntimeError,'checksum'):mod.assemble(self.core)
  self.assertFalse(self.calls)
 def test_interrupted_tree_retained(self):
  staging=self.root/('source-v-test-'+hashlib.sha256(self.package_manifest.read_bytes()).hexdigest()[:16]+'.assembling');staging.mkdir();(staging/'diagnostic').write_text('keep')
  mod.assemble(self.core)
  copies=list(self.root.glob('*.interrupted-*'));self.assertEqual(len(copies),1);self.assertEqual((copies[0]/'diagnostic').read_text(),'keep')
 def test_existing_marker_mismatch_is_not_reset(self):
  source=mod.assemble(self.core);(source/'.oci-release.json').write_text('{}')
  with self.assertRaisesRegex(RuntimeError,'not the selected'):mod.assemble(self.core)
  self.assertEqual((source/'.oci-release.json').read_text(),'{}')
 def test_new_overlay_manifest_gets_a_fresh_source_tree(self):
  first=mod.assemble(self.core);(first/'retained').write_text('old')
  self.package_manifest.write_text('{"fixture":2}')
  second=mod.assemble(self.core)
  self.assertNotEqual(first,second);self.assertEqual((first/'retained').read_text(),'old')
