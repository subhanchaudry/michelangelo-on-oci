import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'installer'))
import support_images
import install
sys.path.insert(0,str(ROOT/'installer/transport'))
import mysql_router


def transport_fixture():
    # Public chart-shaped fixture: exercises the complete fail-closed transform.
    return mysql_router.yaml.safe_dump_all([
        {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'app-config','namespace':'ma-system'},
         'data':{'base.yaml':mysql_router.yaml.safe_dump({'apiserver':{},'mysql':{'host':'127.0.0.1','port':6446}})}},
        {'apiVersion':'apps/v1','kind':'Deployment',
         'metadata':{'name':'michelangelo-apiserver','namespace':'ma-system','labels':{'app.kubernetes.io/part-of':'michelangelo','app.kubernetes.io/component':'apiserver'}},
         'spec':{'template':{'spec':{'containers':[{'name':'apiserver','volumeMounts':[{'name':'config','mountPath':'/config'}]}],
             'volumes':[{'name':'config','configMap':{'name':'app-config'}}],
             'initContainers':[{'name':name,'command':['mysql'], 'env':[{'name':'METADATA_HOST','value':'127.0.0.1'},{'name':'METADATA_PORT','value':'6446'}]} for name in ['wait-for-metadata-storage','schema-init']]}}}}])


def transport_env():
    return {'MYSQL_ROUTER_HOST':'database.example.invalid','MYSQL_ROUTER_CA_SECRET':'database-ca','MYSQL_ROUTER_AUTH_SECRET':'router-auth'}


class SupportImageTests(unittest.TestCase):
    def test_protected_failure_survives_later_recovery_commands(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(install,'WORK',Path(directory)):
            failed=types.SimpleNamespace(returncode=1,stdout='protected rollout diagnostic')
            recovered=types.SimpleNamespace(returncode=0,stdout='recovery completed')
            with patch.object(install.subprocess,'run',side_effect=[failed,recovered]):
                with self.assertRaises(RuntimeError):install.run(['helm','upgrade'],sensitive=True)
                install.run(['helm','rollback'],sensitive=True)
            self.assertEqual((Path(directory)/'last-sensitive-command.log').read_text(),'recovery completed')
            logs=list((Path(directory)/'command-failures').glob('*.log'))
            self.assertEqual(len(logs),1)
            self.assertEqual(logs[0].read_text(),'protected rollout diagnostic')
            self.assertEqual(logs[0].stat().st_mode & 0o777,0o600)

    def test_executable_digest_is_selected_and_kept_out_of_app_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);index='sha256:'+'a'*64;runtime='sha256:'+'b'*64;calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if '--metadata-file' in args:
                    Path(args[args.index('--metadata-file')+1]).write_text(json.dumps({'containerimage.digest':index}))
                else:return json.dumps({'manifests':[{'digest':runtime,'platform':{'os':'linux','architecture':'amd64'}}]})
            saved={}
            def save(path,value):
                saved[path.name]=value;path.write_text(json.dumps(value))
            core=types.SimpleNamespace(WORK=work,ROOT=ROOT/'installer',run=run,save=save)
            config={'registry':'ord.ocir.io/publicfixture/michelangelo/evaluation'}
            manifest=work/'SOURCE-MANIFEST.json';manifest.write_text('{"public":"fixture"}')
            source_digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
            images=support_images.build(core,config,'candidate',source_digest)
            self.assertEqual(config['mysql_router_image'],config['registry']+'@'+runtime)
            self.assertEqual(images['mysql_router']['index_digest'],index)
            self.assertEqual(images['mysql_router']['installer_source_manifest_sha256'],source_digest)
            self.assertEqual(set(saved),{'support-images.json'})
            self.assertIn('--no-cache-filter',calls[0])
            self.assertEqual([p.name for p in (work/'router-build').iterdir()],['Dockerfile'])
            env={**transport_env(),'MYSQL_ROUTER_IMAGE':config['mysql_router_image'],
                 'MYSQL_ROUTER_IMAGE_RECORD':str(work/'support-images.json'),'MYSQL_ROUTER_SOURCE_MANIFEST':str(manifest)}
            rendered=list(mysql_router.yaml.safe_load_all(mysql_router.transform(transport_fixture(),env)))
            router=rendered[1]['spec']['template']['spec']['initContainers'][0]
            self.assertEqual(router['image'],config['mysql_router_image'])
            self.assertEqual(router['restartPolicy'],'Always')
            self.assertTrue(router['securityContext']['readOnlyRootFilesystem'])

    def test_derived_transport_rejects_missing_or_mismatched_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);manifest=work/'manifest.json';manifest.write_text('{}')
            image='ord.ocir.io/publicfixture/michelangelo/evaluation@sha256:'+'b'*64
            record={'pinned':image,'runtime_digest':'sha256:'+'b'*64,'platform':'linux/amd64',
                    'installer_source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
            path=work/'images.json';env={**transport_env(),'MYSQL_ROUTER_IMAGE':image,'MYSQL_ROUTER_IMAGE_RECORD':str(path),'MYSQL_ROUTER_SOURCE_MANIFEST':str(manifest)}
            for change in [{'pinned':image[:-1]+'c'},{'runtime_digest':'sha256:'+'c'*64},{'platform':'linux/arm64'},{'installer_source_manifest_sha256':'c'*64}]:
                path.write_text(json.dumps({'mysql_router':{**record,**change}}))
                with self.subTest(change=change),self.assertRaises(mysql_router.TransportError):mysql_router.transform(transport_fixture(),env)
            path.unlink()
            with self.assertRaises(mysql_router.TransportError):mysql_router.settings(env)
            path.write_text(json.dumps({'mysql_router':record}));manifest.write_text('{"changed":true}')
            with self.assertRaises(mysql_router.TransportError):mysql_router.settings(env)
            env['MYSQL_ROUTER_IMAGE']='docker.io/foreign/router@sha256:'+'b'*64
            with self.assertRaises(mysql_router.TransportError):mysql_router.settings(env)

    def test_stock_transport_remains_supported(self):
        env={**transport_env(),'MYSQL_ROUTER_IMAGE':'container-registry.oracle.com/mysql/community-router@sha256:'+'a'*64}
        self.assertEqual(mysql_router.settings(env)[0],env['MYSQL_ROUTER_IMAGE'])

    def test_router_updates_only_reviewed_libraries_and_keeps_default_identity(self):
        text=(ROOT/'installer/router-runtime.Dockerfile').read_text()
        self.assertIn('@sha256:d704471c2bb78fa833790dc197959c004d09af5aa18f5f153ff40e7268f54043',text)
        command=next(line for line in text.splitlines() if line.startswith('RUN '))
        self.assertNotIn('mysql-router',command)
        self.assertIn("--disablerepo='mysql*'",command)
        self.assertTrue(text.rstrip().endswith('USER 999:999'))


if __name__=='__main__':unittest.main()
