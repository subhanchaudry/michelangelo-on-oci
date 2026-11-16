#!/usr/bin/env python3
"""Reconstruct and test the public source candidate without cloud credentials."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'installer'))


def check(work, prepare_only=False):
    import source_release
    manifest,patch=source_release.manifest()
    source=work/'source'
    if source.exists():raise RuntimeError('Use an empty validation work directory')
    subprocess.run(['git','clone','--depth','1','--branch',manifest['upstream_tag'],manifest['repository'],str(source)],check=True)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    if revision!=manifest['upstream_commit']:raise RuntimeError('Upstream tag changed from pinned source')
    subprocess.run(['git','apply','--check',str(patch.resolve())],cwd=source,check=True)
    subprocess.run(['git','apply',str(patch.resolve())],cwd=source,check=True)
    for _ in range(2):
        for name in ('identity_overlay','identity_ui','service_auth','qualification_overlay','workload_security','dependency_overlay'):
            __import__(name).prepare_source(source)
    if prepare_only:
        print(json.dumps({'upstream_commit':revision,'source_prepared':True,'source_regressions':'not_run'}))
        return
    go=os.environ.get('GO') or shutil.which('go')
    if not go:raise RuntimeError('Go with automatic pinned toolchain selection is required')
    env={**os.environ,'GO':go,'MA_REVIEW_SOURCE_DIR':str(source),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'installer')}
    subprocess.run([go,'test','-tags=kubernetes_protomessage_one_more_release','./auth/...','./api/handler','./api/utils','./components/jobs/client/k8sengine','./components/deployment/...','./components/inferenceserver/...','./components/triggerrun','./worker','./worker/activities/deployment'],cwd=source/'go',env=env,check=True)
    subprocess.run([go,'build','-tags=kubernetes_protomessage_one_more_release','./cmd/apiserver','./cmd/worker','./cmd/controllermgr'],cwd=source/'go',env=env,check=True)
    subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(ROOT/'tests'),'-p','test_workload_security.py'],env=env,check=True)
    subprocess.run([sys.executable,'-B',str(ROOT/'installer/test_service_auth.py')],env=env,check=True)
    print(json.dumps({'upstream_commit':revision,'source_regressions':'passed','cloud_validation':False}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir',type=Path)
    parser.add_argument('--prepare-only',action='store_true',help='Reconstruct for a separate UI validation job; does not run source tests')
    args=parser.parse_args()
    if args.work_dir:
        args.work_dir.mkdir(parents=True,exist_ok=True)
        check(args.work_dir.resolve(), args.prepare_only)
    else:
        with tempfile.TemporaryDirectory() as directory:check(Path(directory), args.prepare_only)


if __name__=='__main__':main()
