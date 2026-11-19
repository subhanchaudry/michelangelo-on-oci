#!/usr/bin/env python3
"""Compile lifecycle workflows with the upstream SDK and a prebuilt task image.

Set PYTHONPATH to the assembled SDK's python/ and demo-source/ directories.
Publishing is explicit; this command never updates live pipelines.
"""
import argparse
import base64
import hashlib
import importlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task-image',required=True)
    p.add_argument('--storage-url',required=True,help='s3://bucket/prefix for workflow packages and outputs')
    p.add_argument('--cluster-target',type=Path,required=True,help='Native ClusterTarget JSON; Secret references only')
    p.add_argument('--registry-endpoint',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--demo')
    p.add_argument('--diagnostic-only',action='store_true',help='Package the preserved controlled-failure entry point with durable log capture')
    p.add_argument('--publish',action='store_true')
    a=p.parse_args()
    if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}',a.task_image):p.error('Task image must be digest pinned')
    if not a.storage_url.startswith('s3://'):p.error('Use an S3-compatible object-storage URL')
    cluster=json.loads(a.cluster_target.read_text())
    if not cluster.get('clusterId') or not cluster.get('kubernetes',{}).get('tokenTag'):p.error('A cluster identity and scoped token Secret reference are required')
    a.output.mkdir(parents=True,exist_ok=False)
    from michelangelo.uniflow.core.build import build
    from michelangelo.gen.api.typed_struct_pb2 import TypedStruct
    import fsspec
    models=json.loads((ROOT/'platform/serving/lifecycle/catalog.json').read_text())['models']
    if a.diagnostic_only:
        if a.demo:p.error('Choose --demo or --diagnostic-only')
        models=[{'demo':'controlled-failure','project':'oci-ranking-showcase','pipelines':[{'name':'controlled-failure','operation':'train-register','type':'PIPELINE_TYPE_TRAIN'}]}]
    if a.demo:
        models=[m for m in models if m['demo']==a.demo]
        if not models:p.error('Unknown demo')
    pipelines=[];evidence=[]
    for model in models:
        demo,namespace=model['demo'],model['project']
        entries=[dict(x,module='oci_demos.workflows' if a.diagnostic_only else 'oci_demos.lifecycle_workflows') for x in model['pipelines']]
        for entry in entries:
            name=entry['name'];function=name.replace('-','_')
            module=importlib.import_module(entry['module'])
            package=build(getattr(module,function)).to_tarball_bytes()
            sha=hashlib.sha256(package).hexdigest()
            local=a.output/(name+'-'+sha+'.tar.gz');local.write_bytes(package)
            uri=a.storage_url.rstrip('/')+'/packages/'+name+'/'+sha+'.tar.gz'
            if a.publish:
                with fsspec.open(uri,'wb') as stream:stream.write(package)
                with fsspec.open(uri,'rb') as stream:
                    if hashlib.sha256(stream.read()).hexdigest()!=sha:raise RuntimeError('Published package integrity mismatch')
            env={'UF_STORAGE_URL':a.storage_url.rstrip('/')+'/'+namespace+'/workflows','UF_TASK_IMAGE':a.task_image,'UF_PLUGIN_RAY_USE_FSSPEC':'1','IMAGE_PULL_POLICY':'IfNotPresent','REGISTRY_NAMESPACE':namespace,'REGISTRY_ENDPOINT':a.registry_endpoint,'MA_PILOT_CLUSTER_TARGET':json.dumps(cluster,separators=(',',':'))}
            env.update(HOME='/tmp',HF_HOME='/tmp/huggingface',XDG_CACHE_HOME='/tmp/cache',GRPC_DNS_RESOLVER='native')
            annotations={'michelangelo/uniflow-image':a.task_image,'michelangelo.ai/demo':demo,'michelangelo.ai/workflow-package-sha256':sha}
            if entry['operation']=='train-register':env['MA_REGISTER_LIFECYCLE']='true'
            else:annotations['michelangelo.ai/lifecycle-operation']=entry['operation']
            config=TypedStruct(type_url='type.googleapis.com/michelangelo/UniFlowConf')
            config.value.update({'environ':env})
            pipelines.append({'apiVersion':'michelangelo.api/v2','kind':'Pipeline','metadata':{'name':name,'namespace':namespace,'annotations':annotations,'labels':{'app.kubernetes.io/part-of':'michelangelo-demo'}},'spec':{'type':entry['type'],'manifest':{'type':'PIPELINE_MANIFEST_TYPE_UNIFLOW','filePath':entry['module'],'uniflowFunction':entry['module']+'.'+function,'uniflowTar':uri,'content':{'@type':'type.googleapis.com/michelangelo.api.TypedStruct','value':base64.b64encode(config.SerializeToString()).decode()}}}})
            evidence.append({'project':namespace,'pipeline':name,'package_sha256':sha,'uri':uri,'published':a.publish})
    (a.output/'pipelines.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':pipelines},indent=2)+'\n')
    (a.output/'packages.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'pipelines':len(pipelines),'published':a.publish,'live_pipelines_changed':False}))

if __name__=='__main__':main()
