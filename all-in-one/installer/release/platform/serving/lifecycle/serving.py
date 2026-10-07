"""Operator actions against native Michelangelo targets and deployments."""
from __future__ import annotations
import importlib
import json
import math
import os
import time
from urllib.request import Request, urlopen
import grpc
from google.protobuf.json_format import ParseDict
from registry import context
from scoring import TOLERANCES


class NativeService:
    def __init__(self, kind, field):
        self.kind, self.field = kind, field
        self.messages = importlib.import_module('michelangelo.gen.api.v2.'+field+'_svc_pb2')
        self.resource = getattr(importlib.import_module('michelangelo.gen.api.v2.'+field+'_pb2'), kind)
        self.channel = grpc.insecure_channel(os.environ['REGISTRY_ENDPOINT'])
        self.stub = getattr(importlib.import_module('michelangelo.gen.api.v2.'+field+'_svc_pb2_grpc'), kind+'ServiceStub')(self.channel)
    def call(self, action, **kwargs):
        request = getattr(self.messages, action+self.kind+'Request')(**kwargs)
        response = getattr(self.stub, action+self.kind)(request, timeout=30, metadata=(('rpc-caller','oci-lifecycle-pilot'),('rpc-service',os.environ.get('MA_API_SERVER_NAME','ma-apiserver')),('rpc-encoding','proto')))
        return getattr(response, self.field)
    def get(self, namespace, name): return self.call('Get', namespace=namespace, name=name)
    def create(self, value): return self.call('Create', **{self.field: ParseDict(value, self.resource())})
    def update(self, value): return self.call('Update', **{self.field: value})
    def mutate(self, namespace, name, change):
        # Status reconciliation advances resourceVersion between operator reads.
        # Re-read on conflict and reapply only this operator's intended fields.
        for attempt in range(8):
            current = self.get(namespace, name)
            change(current)
            try: return self.update(current)
            except grpc.RpcError as exc:
                conflict = exc.code() in (grpc.StatusCode.ABORTED, grpc.StatusCode.FAILED_PRECONDITION) and 'object has been modified' in exc.details()
                if not conflict or attempt == 7: raise
                time.sleep(min(0.2 * (2 ** attempt), 2))
    def close(self): self.channel.close()


def wait_for(get, predicate, description, seconds=900):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        value = get()
        if predicate(value): return value
        time.sleep(5)
    raise TimeoutError('Timed out waiting for '+description)


def inference(base, revision, records):
    # Model readiness is independent of Kubernetes and server health readiness.
    with urlopen(base+'/v2/models/'+revision+'/ready', timeout=15) as response:
        if response.status != 200: raise RuntimeError('Model is not ready')
    request = {'inputs':[{'name':'RECORDS','shape':[1],'datatype':'BYTES','data':[json.dumps(records, allow_nan=False)]}]}
    with urlopen(Request(base+'/v2/models/'+revision+'/infer', data=json.dumps(request).encode(), headers={'Content-Type':'application/json'}), timeout=60) as response:
        output = json.load(response)['outputs']
    scores = next(x for x in output if x['name']=='SCORES')
    if scores['datatype']!='FP64': raise ValueError('Unexpected inference output type')
    if scores['shape']==[len(records)]: return scores['data']
    if scores['shape']==[len(records),2]: return [scores['data'][i:i+2] for i in range(0,len(scores['data']),2)]
    raise ValueError('Unexpected inference output shape')


def assert_parity(demo, actual, expected):
    if len(actual)!=len(expected): raise ValueError('Batch/online result count differs')
    tolerance=TOLERANCES[demo]
    for a,b in zip(actual,expected):
        aa=a if isinstance(a,list) else [a]; bb=b if isinstance(b,list) else [b]
        if len(aa)!=len(bb) or any(not math.isfinite(x) or not math.isclose(x,y,rel_tol=tolerance['rtol'],abs_tol=tolerance['atol']) for x,y in zip(aa,bb)):
            raise ValueError('Batch/online predictions exceed documented tolerance')


def transition(demo, action, revision, check, phase='full'):
    if phase not in ('full', 'prepare', 'verify'): raise ValueError('Unknown serving phase')
    if action not in ('deploy','rollback','deactivate'): raise ValueError('Explicit deploy, rollback, or deactivate action required')
    namespace, run = context(); target_name=demo+'-cpu'; deployment_name=demo+'-deployment'
    targets=NativeService('InferenceServer','inference_server'); deployments=NativeService('Deployment','deployment')
    try:
        try: target=targets.get(namespace,target_name)
        except grpc.RpcError as exc:
            if exc.code()!=grpc.StatusCode.NOT_FOUND or action!='deploy': raise
            connection=json.loads(os.environ['MA_PILOT_CLUSTER_TARGET'])
            target=targets.create({'metadata':{'name':target_name,'namespace':namespace,'labels':{'michelangelo/environment':'nonprod'}},
                'spec':{'tenancyType':'TENANCY_TYPE_DEDICATED','backendType':'BACKEND_TYPE_TRITON',
                        'initSpec':{'numInstances':1},'clusterTargets':[connection],'suspended':True}})
        if action=='deactivate':
            targets.mutate(namespace,target_name,lambda x:setattr(x.spec,'suspended',True))
            observed=wait_for(lambda:targets.get(namespace,target_name),lambda x:x.status.state==9 and x.status.observed_generation>=x.metadata.generation,'target suspension and compute release')
            return {'action':action,'target':target_name,'state':'inactive','observed_generation':observed.status.observed_generation,'source_run':run}
        if revision is None or check is None: raise ValueError('Deployment requires a validated model and parity request')
        try: deployment=deployments.get(namespace,deployment_name)
        except grpc.RpcError as exc:
            if exc.code()!=grpc.StatusCode.NOT_FOUND or action!='deploy': raise
            deployment=None
        if action=='rollback':
            # A rollback must reference a revision previously selected by this deployment.
            history=json.loads(deployment.metadata.annotations.get('michelangelo.ai/operator-revisions','[]'))
            if revision.metadata.name not in history: raise ValueError('Rollback revision was never selected on this deployment')
        if phase != 'full' and deployment is None:
            raise ValueError('Native serving requires a configured deployment template for this model')
        if phase=='verify':
            if target.spec.suspended or target.status.state!=3:
                raise ValueError('Target is not active for inference verification')
            if deployment.status.state!=2 or deployment.status.current_revision.name!=revision.metadata.name or deployment.spec.desired_revision.name!=revision.metadata.name:
                raise ValueError('Native deployment did not select the requested revision')
            return verify_transition(demo, action, revision, check, namespace, run, target_name, deployment_name, deployments)
        targets.mutate(namespace,target_name,lambda x:setattr(x.spec,'suspended',False))
        wait_for(lambda:targets.get(namespace,target_name),lambda x:x.status.state==3 and x.status.observed_generation>=x.metadata.generation,'native target serving')
        if phase=='prepare':
            return {'namespace':namespace,'deployment_name':deployment_name,'model_revision':revision.metadata.name,'action':action,'target':target_name}
        selected={'name':revision.metadata.name,'namespace':namespace}
        if deployment is None:
            deployment=deployments.create({'metadata':{'name':deployment_name,'namespace':namespace},'spec':{
                'desiredRevision':selected,'inferenceServer':{'name':target_name,'namespace':namespace},
                'modelFamily':{'name':demo,'namespace':namespace},'definition':{'type':'TARGET_TYPE_INFERENCE_SERVER','subType':'realtime-serving'},
                'strategy':{'rolling':{'incrementPercentage':100}}}})
        else:
            def select_revision(current):
                current.spec.definition.sub_type="realtime-serving"
                current.spec.desired_revision.name=revision.metadata.name
                current.spec.desired_revision.namespace=namespace
            deployment=deployments.mutate(namespace,deployment_name,select_revision)
        observed=wait_for(lambda:deployments.get(namespace,deployment_name),lambda x:x.status.state==2 and x.status.current_revision.name==revision.metadata.name,'native model rollout')
        return verify_transition(demo, action, revision, check, namespace, run, target_name, deployment_name, deployments)
    finally:
        targets.close();deployments.close()


def verify_transition(demo, action, revision, check, namespace, run, target_name, deployment_name, deployments):
    base='http://'+target_name+'-inference-service.'+namespace+'.svc.cluster.local'
    actual=[];size=16 if demo=='bert-cola-finetuning' else 1024
    for start in range(0,len(check['records']),size): actual.extend(inference(base,revision.metadata.name,check['records'][start:start+size]))
    assert_parity(demo,actual,check['expected'])
    # Record only verified revisions, after readiness and real inference passed.
    def record_verified(current):
        history=json.loads(current.metadata.annotations.get('michelangelo.ai/operator-revisions','[]'))
        if revision.metadata.name not in history: history.append(revision.metadata.name)
        current.metadata.annotations['michelangelo.ai/operator-revisions']=json.dumps(history)
        current.metadata.annotations['michelangelo.ai/last-operator-run']=run
    deployments.mutate(namespace,deployment_name,record_verified)
    return {'action':action,'target':target_name,'deployment':deployment_name,'model_revision':revision.metadata.name,
            'model_ready':True,'parity_passed':True,'predictions':actual,'tolerance':TOLERANCES[demo],'source_run':run}
