import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'installer/image_provenance.py'
spec=importlib.util.spec_from_file_location('image_provenance',path)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class ImageProvenanceTests(unittest.TestCase):
 def test_attestation_changes_do_not_change_executable_identity(self):
  runtime='sha256:'+'a'*64
  document={'manifests':[{'digest':runtime,'platform':{'os':'linux','architecture':'amd64'}},{'digest':'sha256:'+'b'*64,'platform':{'os':'unknown','architecture':'unknown'}}]}
  self.assertEqual(mod.runtime_digest(document,'sha256:'+'c'*64),runtime)
  document['manifests'][1]['digest']='sha256:'+'d'*64
  self.assertEqual(mod.runtime_digest(document,'sha256:'+'e'*64),runtime)
 def test_missing_or_ambiguous_runtime_refused(self):
  item={'digest':'sha256:'+'a'*64,'platform':{'os':'linux','architecture':'amd64'}}
  for manifests in ([],[item,item],[{'digest':'invalid','platform':item['platform']}]):
   with self.subTest(manifests=manifests),self.assertRaises(ValueError):mod.runtime_digest({'manifests':manifests},'sha256:'+'f'*64)
 def test_single_manifest_retained(self):
  digest='sha256:'+'a'*64
  self.assertEqual(mod.runtime_digest({'mediaType':'application/vnd.oci.image.manifest.v1+json'},digest),digest)
 def test_non_manifest_refused(self):
  with self.assertRaises(ValueError):mod.runtime_digest({'mediaType':'application/json'},'sha256:'+'f'*64)
 def test_mutable_reference_refused(self):
  with self.assertRaises(ValueError):mod.runtime_digest({'mediaType':'application/vnd.oci.image.manifest.v1+json'},'latest')
