"""Checkpoint and drain the isolated application before configuration changes."""
import datetime
import json
import time
import subprocess
from pathlib import Path

CONTROL = 'ma-release-control'
ADMISSION = 'ma-upgrade-maintenance'
RESOURCES = [('clusterpolicies.kyverno.io',None),('pipelines.michelangelo.api','all'),
 ('inferenceservers.michelangelo.api','all'),('deployments.michelangelo.api','all'),
 ('clusters.michelangelo.api','all'),('configmaps','all')]


def get(core, resource, namespace='all'):
    command = ['kubectl','get',resource,'-o','json']
    if namespace == 'all': command += ['-A']
    elif namespace: command += ['-n',namespace]
    return json.loads(core.run(command,capture=True))


def set_gate(core, enabled):
    core.apply({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':CONTROL,'namespace':core.NS},
                'data':{'maintenance':'true' if enabled else 'false'}})


def admission_documents():
    """Native admission protects the first upgrade, before the API has a gate.

    No caller-supplied annotation or shared API service account bypasses it.
    Status writes, cancellation, pausing, and reads remain available.
    """
    unchanged = lambda field: f"(has(object.spec.{field}) == has(oldObject.spec.{field}) && (!has(object.spec.{field}) || object.spec.{field} == oldObject.spec.{field}))"
    # The API overwrites these stamps from TokenReview, including on updates.
    # Require its Kubernetes principal too; arbitrary CRD writers cannot forge it.
    worker = "(request.userInfo.username == 'system:serviceaccount:ma-system:michelangelo' && has(object.metadata.annotations) && 'identity.michelangelo.ai/actor-kind' in object.metadata.annotations && object.metadata.annotations['identity.michelangelo.ai/actor-kind'] == 'service' && 'identity.michelangelo.ai/actor-subject' in object.metadata.annotations && object.metadata.annotations['identity.michelangelo.ai/actor-subject'] == 'system:serviceaccount:ma-system:michelangelo')"
    spark_unchanged = ' && '.join(unchanged(field) for field in ('user','affinity','driver','executor','sparkConf','mainClass','mainApplicationFile','mainArgs','deps','scheduling','sparkVersion'))
    allowed = (
        "request.resource.resource == 'pipelineruns' ? "
        f"(request.operation == 'UPDATE' && {unchanged('resume')} && {unchanged('retryInfo')}) : "
        "request.resource.resource == 'rayjobs' ? "
        f"({worker} || (request.operation == 'UPDATE' && object.spec == oldObject.spec)) : "
        "request.resource.resource == 'sparkjobs' ? "
        f"({worker} || (request.operation == 'UPDATE' && {spark_unchanged})) : "
        "request.resource.resource == 'triggerruns' ? "
        "(request.operation == 'UPDATE' && (!has(object.spec.action) || object.spec.action != 'TRIGGER_RUN_ACTION_RESUME' || (has(oldObject.spec.action) && object.spec.action == oldObject.spec.action))) : "
        "request.resource.resource == 'inferenceservers' ? "
        "(has(object.spec.suspended) && object.spec.suspended) : "
        f"(!has(object.spec.desiredRevision) || (request.operation == 'UPDATE' && {unchanged('desiredRevision')}))"
    )
    return [
        {'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicy',
         'metadata':{'name':ADMISSION},'spec':{'failurePolicy':'Fail',
            'matchConstraints':{'resourceRules':[{'apiGroups':['michelangelo.api'],'apiVersions':['*'],
                'operations':['CREATE','UPDATE'],'resources':['pipelineruns','triggerruns','inferenceservers','deployments','sparkjobs','rayjobs']}]},
            'validations':[{'expression':allowed,'message':'Michelangelo maintenance blocks new work'}]}},
        {'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicyBinding',
         'metadata':{'name':ADMISSION},'spec':{'policyName':ADMISSION,'validationActions':['Deny']}}
    ]


def native_gate(core):
    core.apply(admission_documents())


def verify_native_gate(core):
    # A dry run must be denied by this policy, not by some unrelated schema error.
    probe={'apiVersion':'michelangelo.api/v2','kind':'PipelineRun',
           'metadata':{'name':'ma-maintenance-admission-probe','namespace':'default'},'spec':{}}
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        result=subprocess.run(['kubectl','create','--dry-run=server','-f','-'],input=json.dumps(probe),capture_output=True,text=True)
        if result.returncode and 'Michelangelo maintenance blocks new work' in result.stderr:return
        time.sleep(2)
    raise RuntimeError('Native maintenance denial was not verified; upgrade stopped')


def allow_installer_probe(core):
    """Only after the gated API/controller images are ready; API remains paused."""
    core.run(['kubectl','delete','validatingadmissionpolicybinding',ADMISSION,'--ignore-not-found'])


def terminal(run):
    return run.get('status',{}).get('state') in ('PIPELINE_RUN_STATE_SUCCEEDED','PIPELINE_RUN_STATE_FAILED','PIPELINE_RUN_STATE_KILLED','PIPELINE_RUN_STATE_SKIPPED',3,4,5,6)


def active_compute(core):
    rays = get(core,'rayjobs.michelangelo.api').get('items',[])
    sparks = get(core,'sparkjobs.michelangelo.api').get('items',[])
    active = [x for x in rays if x.get('status',{}).get('state') not in ('RAY_JOB_STATE_SUCCEEDED','RAY_JOB_STATE_FAILED','RAY_JOB_STATE_KILLED',3,4,5)]
    for job in sparks:
        conditions = job.get('status',{}).get('statusConditions',[])
        if not any((c.get('type') == 'Succeeded' and c.get('status') in ('CONDITION_STATUS_TRUE','CONDITION_STATUS_FALSE',1,2)) or
                   (c.get('type') == 'Killed' and c.get('status') in ('CONDITION_STATUS_TRUE',1)) for c in conditions):
            active.append(job)
    return active


def begin(core):
    """Never drain by killing work. A timeout leaves maintenance enabled."""
    set_gate(core, True)
    pointer = core.WORK/'active-upgrade-checkpoint.json'
    if pointer.exists():
        active=json.loads(pointer.read_text())
        directory=Path(active['directory']).resolve()
        if directory.parent != (core.WORK/'upgrade-checkpoints').resolve():
            raise RuntimeError('Invalid active checkpoint location; maintenance retained')
        checkpoint=json.loads((directory/'checkpoint.json').read_text())
        if not checkpoint.get('ready') or not checkpoint.get('helm_revision'):
            raise RuntimeError('Incomplete active checkpoint; maintenance retained')
        native_gate(core)
        verify_native_gate(core)
        drain(core)
        return directory
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    directory = core.WORK/'upgrade-checkpoints'/stamp
    directory.mkdir(parents=True,mode=0o700)
    history = subprocess.run(['helm','history',core.APP,'-n',core.NS,'-o','json'],capture_output=True,text=True)
    if history.returncode:
        if 'release: not found' not in history.stderr:
            raise RuntimeError('Unable to establish prior release history; maintenance retained')
        records=[]
    else:
        records=json.loads(history.stdout)
        if not isinstance(records,list) or not records:
            raise RuntimeError('Invalid prior release history; maintenance retained')
    revision = max((int(x['revision']) for x in records if x.get('status') == 'deployed'),default=None)
    if records and revision is None:
        raise RuntimeError('Prior release has no deployed revision; inspect interrupted upgrade before retrying')
    checkpoint = {'helm_revision':revision,'resources':[],'triggers':[],'ready':False}
    core.save(directory/'checkpoint.json',checkpoint)
    # Fresh install: no CRDs or application history exists yet.
    if revision is None:
        return directory
    native_gate(core)
    verify_native_gate(core)
    for resource,namespace in RESOURCES:
        for item in get(core,resource,namespace).get('items',[]):
            meta=item.get('metadata',{});name=meta.get('name','')
            if resource=='clusterpolicies.kyverno.io' and name!='michelangelo-lifecycle-runtime-access': continue
            if resource=='configmaps' and name not in ('ma-lifecycle-runtime-artifacts','michelangelo-runtime-artifacts'):continue
            checkpoint['resources'].append(item)
    # Overlay deployments live outside Helm. Preserve their code and image
    # together; the identity database itself is never part of this rollback.
    checkpoint['overlay_deployments']=[]
    for item in get(core,'deployments.apps',core.NS).get('items',[]):
        if item['metadata']['name'].startswith('ma-'):
            checkpoint['overlay_deployments'].append(item['metadata']['name'])
            checkpoint['resources'].append(item)
    for item in get(core,'configmaps',core.NS).get('items',[]):
        name=item['metadata']['name']
        if name.startswith('ma-') and name != CONTROL and not any(x['kind']=='ConfigMap' and x['metadata']['name']==name and x['metadata'].get('namespace')==core.NS for x in checkpoint['resources']):
            checkpoint['resources'].append(item)
    for trigger in get(core,'triggerruns.michelangelo.api').get('items',[]):
        state=trigger.get('status',{}).get('state')
        if state not in ('TRIGGER_RUN_STATE_PAUSED','TRIGGER_RUN_STATE_SUCCEEDED','TRIGGER_RUN_STATE_FAILED','TRIGGER_RUN_STATE_KILLED',2,3,4,6):
            checkpoint['triggers'].append(trigger)
            core.save(directory/'checkpoint.json',checkpoint)
            meta=trigger['metadata']
            core.run(['kubectl','-n',meta['namespace'],'patch','triggerruns.michelangelo.api',meta['name'],'--type=merge','--patch-file=/dev/stdin'],stdin=json.dumps({'spec':{'action':'TRIGGER_RUN_ACTION_PAUSE'}}),sensitive=True)
    core.save(directory/'checkpoint.json',checkpoint)
    drain(core)
    checkpoint['ready']=True
    core.save(directory/'checkpoint.json',checkpoint)
    # Keep the original compatible configuration across retries. Never snapshot
    # a partially upgraded policy/image set as the next rollback destination.
    temporary=pointer.with_suffix('.tmp')
    core.save(temporary,{'directory':str(directory.resolve())})
    temporary.replace(pointer)
    return directory


def drain(core):
    deadline=time.monotonic()+1800
    while time.monotonic()<deadline:
        runs=get(core,'pipelineruns.michelangelo.api').get('items',[])
        active=[x for x in runs if not terminal(x)]
        triggers=get(core,'triggerruns.michelangelo.api').get('items',[])
        paused=all(x.get('status',{}).get('state') in ('TRIGGER_RUN_STATE_PAUSED','TRIGGER_RUN_STATE_SUCCEEDED','TRIGGER_RUN_STATE_FAILED','TRIGGER_RUN_STATE_KILLED',2,3,4,6) for x in triggers)
        if not active and paused and not active_compute(core):break
        time.sleep(5)
    else:raise RuntimeError('Drain timed out; maintenance retained and existing work was not killed')
    targets=get(core,'inferenceservers.michelangelo.api').get('items',[])
    if any(not x.get('spec',{}).get('suspended') for x in targets):
        raise RuntimeError('Serving targets must be suspended before an installer upgrade; maintenance retained')


def complete(core):
    (core.WORK/'active-upgrade-checkpoint.json').unlink(missing_ok=True)


def rollback(core, directory):
    """Restore application configuration only; never restore a database implicitly."""
    checkpoint=json.loads((directory/'checkpoint.json').read_text())
    set_gate(core,True)
    native_gate(core)
    verify_native_gate(core)
    revision=checkpoint.get('helm_revision')
    if not revision or not checkpoint.get('ready'):
        raise RuntimeError('No complete rollback checkpoint; maintenance retained')
    if any(not terminal(x) for x in get(core,'pipelineruns.michelangelo.api').get('items',[])) or active_compute(core):
        raise RuntimeError('Rollback refuses active workflows; maintenance retained')
    for item in checkpoint['resources']:
        meta=item['metadata']
        group=item['apiVersion'].split('/')[0] if '/' in item['apiVersion'] else ''
        resource=item['kind']+('.'+group if group else '')
        command=['kubectl','get',resource,meta['name'],'-o','json']
        if meta.get('namespace'):command+=['-n',meta['namespace']]
        current=json.loads(core.run(command,capture=True))
        if not meta.get('uid') or current.get('metadata',{}).get('uid') != meta['uid']:
            raise RuntimeError('Rollback resource identity changed; refusing to recreate historical objects')
    if 'ma-publication-controller' not in checkpoint.get('overlay_deployments',[]):
        current=get(core,'deployments.apps',core.NS).get('items',[])
        if any(x['metadata']['name']=='ma-publication-controller' for x in current):
            core.run(['kubectl','-n',core.NS,'scale','deployment/ma-publication-controller','--replicas=0'])
    for item in checkpoint['resources']:
        # Apply desired state with fresh optimistic concurrency; preserve actual IDs.
        item.pop('status',None)
        meta=item['metadata']
        for field in ('resourceVersion','uid','creationTimestamp','generation','managedFields','selfLink'):
            meta.pop(field,None)
        meta.get('annotations',{}).pop('kubectl.kubernetes.io/last-applied-configuration',None)
        core.apply(item)
    core.run(['helm','rollback',core.APP,str(revision),'-n',core.NS,'--wait','--timeout=15m'])
    core.save(directory/'rollback.json',{'configuration_restored':True,'database_restored':False,'maintenance':True,'schedules_remain_paused':True})
    complete(core)
