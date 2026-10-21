import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'installer'))
import support_images


class SupportImageTests(unittest.TestCase):
    def test_executable_digest_is_selected_and_kept_out_of_app_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);index='sha256:'+'a'*64;runtime='sha256:'+'b'*64;calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if '--metadata-file' in args:
                    Path(args[args.index('--metadata-file')+1]).write_text(json.dumps({'containerimage.digest':index}))
                else:return json.dumps({'manifests':[{'digest':runtime,'platform':{'os':'linux','architecture':'amd64'}}]})
            saved={}
            core=types.SimpleNamespace(WORK=work,ROOT=ROOT/'installer',run=run,save=lambda path,value:saved.update({path.name:value}))
            config={'registry':'registry.example.invalid/evaluation'}
            images=support_images.build(core,config,'candidate','c'*64)
            self.assertEqual(config['mysql_router_image'],'registry.example.invalid/evaluation@'+runtime)
            self.assertEqual(images['mysql_router']['index_digest'],index)
            self.assertEqual(images['mysql_router']['installer_source_manifest_sha256'],'c'*64)
            self.assertEqual(set(saved),{'support-images.json'})
            self.assertIn('--no-cache-filter',calls[0])
            self.assertEqual([p.name for p in (work/'router-build').iterdir()],['Dockerfile'])

    def test_router_updates_only_reviewed_libraries_and_keeps_default_identity(self):
        text=(ROOT/'installer/router-runtime.Dockerfile').read_text()
        self.assertIn('@sha256:d704471c2bb78fa833790dc197959c004d09af5aa18f5f153ff40e7268f54043',text)
        command=next(line for line in text.splitlines() if line.startswith('RUN '))
        self.assertNotIn('mysql-router',command)
        self.assertIn("--disablerepo='mysql*'",command)
        self.assertTrue(text.rstrip().endswith('USER 999:999'))


if __name__=='__main__':unittest.main()
