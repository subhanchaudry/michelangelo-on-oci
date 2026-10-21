"""Native, fail-closed validation for the default-namespace runtime profile."""
import json
import copy
from pathlib import Path
import subprocess
import time

POLICY = 'ma-runtime-containment'
ACCOUNT = 'michelangelo-compute-manager'
RUNNER = 'michelangelo-lifecycle-runner'


def prepare_source(source):
    from identity_overlay import replace
    directory = Path(source)/'go/components/jobs/client/k8sengine'
    (directory/'runtime_security.go').write_bytes((Path(__file__).parent/'runtime_template.go.txt').read_bytes())
    ray = directory/'ray.go'
    replace(ray, '\treturn rayV1Cluster, nil', '''\tsecureRuntimeTemplate(&rayV1Cluster.Spec.HeadGroupSpec.Template)
    for i := range rayV1Cluster.Spec.WorkerGroupSpecs { secureRuntimeTemplate(&rayV1Cluster.Spec.WorkerGroupSpecs[i].Template) }
    return rayV1Cluster, nil''', 'secureRuntimeTemplate(&rayV1Cluster.Spec.HeadGroupSpec.Template)')
    tests = directory/'mapper_test.go'
    text = tests.read_text()
    if 'secureRuntimeExpectation' not in text:
        text = text.replace('require.Equal(t, tt.expectedLocalObject, ', 'secureRuntimeExpectation(tt.expectedLocalObject.(k8sruntime.Object))\n            require.Equal(t, tt.expectedLocalObject, ')
        text += '\nfunc secureRuntimeExpectation(obj k8sruntime.Object) {\n switch expected := obj.(type) {\n case *rayv1.RayJob: secureRuntimeTemplate(expected.Spec.SubmitterPodTemplate)\n case *rayv1.RayCluster:\n  secureRuntimeTemplate(&expected.Spec.HeadGroupSpec.Template)\n  for i := range expected.Spec.WorkerGroupSpecs { secureRuntimeTemplate(&expected.Spec.WorkerGroupSpecs[i].Template) }\n }\n}\n'
        tests.write_text(text)
    # Submitters are constructed from the global head, before local mutation.
    replace(ray, '\treturn kubeRayJob, nil', '''\tsecureRuntimeTemplate(kubeRayJob.Spec.SubmitterPodTemplate)
    return kubeRayJob, nil''', 'secureRuntimeTemplate(kubeRayJob.Spec.SubmitterPodTemplate)')


def runtime_expression(image, final=False):
    """p is a PodSpec. Enumerate allowed volume/source forms instead of a denylist."""
    env = "(!has(c.envFrom) || c.envFrom.all(e, (!has(e.prefix) || e.prefix == '') && ((has(e.secretRef) && e.secretRef.name == 'ma-lifecycle-runtime-artifacts' && !has(e.configMapRef)) || (has(e.configMapRef) && e.configMapRef.name == 'ma-lifecycle-runtime-artifacts' && !has(e.secretRef)))))"
    keyed = "(!has(c.env) || c.env.all(e, !has(e.valueFrom) || ((!has(e.valueFrom.secretKeyRef)) && (!has(e.valueFrom.configMapKeyRef) || e.valueFrom.configMapKeyRef.name == 'ma-lifecycle-runtime-artifacts'))))"
    container = f"c.image == {json.dumps(image)} && has(c.securityContext) && has(c.securityContext.allowPrivilegeEscalation) && !c.securityContext.allowPrivilegeEscalation && (!has(c.securityContext.privileged) || !c.securityContext.privileged) && (!has(c.securityContext.runAsUser) || c.securityContext.runAsUser != 0) && has(c.securityContext.capabilities) && (!has(c.securityContext.capabilities.add) || size(c.securityContext.capabilities.add) == 0) && has(c.securityContext.capabilities.drop) && 'ALL' in c.securityContext.capabilities.drop && (!has(c.securityContext.procMount) || c.securityContext.procMount == 'Default') && (!has(c.securityContext.seccompProfile) || c.securityContext.seccompProfile.type == 'RuntimeDefault') && {env} && {keyed} && (!has(c.volumeDevices) || size(c.volumeDevices) == 0) && (!has(c.ports) || c.ports.all(port, (!has(port.hostPort) || port.hostPort == 0) && (!has(port.hostIP) || port.hostIP == '')))"
    # Only the API server's standard bounded projection is allowed in final Pods.
    projected = ""
    if final:
        projected = " || (v.name.startsWith('kube-api-access-') && has(v.projected) && size(v.projected.sources) == 3 && v.projected.sources.all(s, (has(s.serviceAccountToken) && s.serviceAccountToken.path == 'token' && (!has(s.serviceAccountToken.audience) || s.serviceAccountToken.audience == 'https://kubernetes.default.svc.cluster.local') && s.serviceAccountToken.expirationSeconds <= 7200) || (has(s.configMap) && s.configMap.name == 'kube-root-ca.crt' && size(s.configMap.items) == 1 && s.configMap.items[0].key == 'ca.crt' && s.configMap.items[0].path == 'ca.crt') || (has(s.downwardAPI) && size(s.downwardAPI.items) == 1 && s.downwardAPI.items[0].path == 'namespace' && s.downwardAPI.items[0].fieldRef.fieldPath == 'metadata.namespace')) && v.projected.sources.exists(s, has(s.serviceAccountToken)) && v.projected.sources.exists(s, has(s.configMap)) && v.projected.sources.exists(s, has(s.downwardAPI)))"
    node = "(!has(p.nodeName) || p.nodeName == '')"
    if final:
        node = "(!has(p.nodeName) || p.nodeName == '' || (request.operation == 'UPDATE' && has(oldObject.spec.nodeName) && p.nodeName == oldObject.spec.nodeName))"
    return f"""has(p.serviceAccountName) && p.serviceAccountName == '{RUNNER}'
 && (!has(p.serviceAccount) || p.serviceAccount == '' || p.serviceAccount == '{RUNNER}')
 && (!has(p.hostNetwork) || !p.hostNetwork) && (!has(p.hostPID) || !p.hostPID) && (!has(p.hostIPC) || !p.hostIPC)
 && {node}
 && has(p.securityContext) && has(p.securityContext.runAsNonRoot) && p.securityContext.runAsNonRoot
 && (!has(p.securityContext.runAsUser) || p.securityContext.runAsUser != 0)
 && has(p.securityContext.seccompProfile) && p.securityContext.seccompProfile.type == 'RuntimeDefault'
 && (!has(p.securityContext.sysctls) || size(p.securityContext.sysctls) == 0)
 && has(p.containers) && size(p.containers) > 0 && p.containers.all(c, {container})
 && (!has(p.initContainers) || p.initContainers.all(c, {container}))
 && (!has(p.ephemeralContainers) || size(p.ephemeralContainers) == 0)
 && (!has(p.imagePullSecrets) || p.imagePullSecrets.all(s, s.name == 'ma-lifecycle-runtime-registry'))
 && (!has(p.volumes) || p.volumes.all(v, has(v.emptyDir){projected}))""".replace('\n', ' ')


def renewal_expression(final=False):
    # Derive exact allowed programs from the reviewed template, never labels or names.
    import yaml
    template = (Path(__file__).parent/'release/platform/releases/v0.11.0/token-renewal.example.yaml').read_text()
    alternatives = []
    for account in ('michelangelo-compute-manager', 'michelangelo-serving-manager'):
        text = template
        for old, new in {'REPLACE_RENEWER_NAME':account+'-renewer','REPLACE_RUNTIME_NAMESPACE':'default',
                         'REPLACE_RUNTIME_ACCOUNT':account,'REPLACE_TOKEN_SECRET':'cluster-oci-'+('compute' if account == ACCOUNT else 'serving')+'-client-token',
                         'REPLACE_RENEWAL_JOB_NAME':account+'-token-renew',
                         'REPLACE_SYSTEM_NODE_LABEL':'workload.oci.oracle.com/pool','REPLACE_SYSTEM_NODE_VALUE':'system'}.items():
            text = text.replace(old,new)
        cron = list(yaml.safe_load_all(text))[-1]
        pod = cron['spec']['jobTemplate']['spec']['template']['spec']
        c = pod['containers'][0]
        # Explicit fields tolerate only Kubernetes defaulting, never added env/mounts.
        checks = [f"p.serviceAccountName == {json.dumps(account+'-renewer')}",
                  "size(p.containers) == 1 && p.containers[0].name == 'renew'", f"p.containers[0].image == {json.dumps(c['image'])}",
                  f"p.containers[0].command == {json.dumps(c['command'])}",
                  f"p.containers[0].args == {json.dumps(c['args'])}",
                  "(!has(p.containers[0].env) || size(p.containers[0].env) == 0)",
                  "(!has(p.containers[0].envFrom) || size(p.containers[0].envFrom) == 0)",
                  "p.containers[0].securityContext.allowPrivilegeEscalation == false",
                  "p.containers[0].securityContext.readOnlyRootFilesystem == true",
                  "(!has(p.containers[0].securityContext.privileged) || !p.containers[0].securityContext.privileged)",
                  "p.containers[0].securityContext.capabilities.drop == ['ALL']",
                  "(!has(p.containers[0].securityContext.capabilities.add) || size(p.containers[0].securityContext.capabilities.add) == 0)",
                  "(!has(p.containers[0].securityContext.runAsUser) || p.containers[0].securityContext.runAsUser == 65532)",
                  "(!has(p.containers[0].securityContext.runAsNonRoot) || p.containers[0].securityContext.runAsNonRoot)",
                  "(!has(p.containers[0].securityContext.procMount) || p.containers[0].securityContext.procMount == 'Default')",
                  "(!has(p.containers[0].securityContext.seccompProfile) || p.containers[0].securityContext.seccompProfile.type == 'RuntimeDefault')",
                  "!has(p.containers[0].lifecycle) && !has(p.containers[0].livenessProbe) && !has(p.containers[0].readinessProbe) && !has(p.containers[0].startupProbe)",
                  "(!has(p.containers[0].volumeDevices) || size(p.containers[0].volumeDevices) == 0)",
                  "p.securityContext.runAsNonRoot && p.securityContext.runAsUser == 65532 && p.securityContext.runAsGroup == 65532",
                  "p.securityContext.seccompProfile.type == 'RuntimeDefault'",
                  "(!has(p.securityContext.sysctls) || size(p.securityContext.sysctls) == 0)",
                  "p.restartPolicy == 'Never' && size(p.nodeSelector) == 1 && p.nodeSelector['workload.oci.oracle.com/pool'] == 'system'",
                  "(!has(p.hostNetwork) || !p.hostNetwork) && (!has(p.hostPID) || !p.hostPID) && (!has(p.hostIPC) || !p.hostIPC)",
                  "(!has(p.initContainers) || size(p.initContainers) == 0) && (!has(p.ephemeralContainers) || size(p.ephemeralContainers) == 0)",
                  "(!has(p.imagePullSecrets) || size(p.imagePullSecrets) == 0)"]
        if final:
            # All mounts/volumes must be the standard API projection. The exact
            # renewer SA cannot be selected by the compute manager's remaining APIs.
            checks += ["has(p.volumes) && size(p.volumes) == 1 && p.volumes[0].name.startsWith('kube-api-access-') && has(p.volumes[0].projected)",
                       "size(p.volumes[0].projected.sources) == 3 && p.volumes[0].projected.sources.all(s, (has(s.serviceAccountToken) && s.serviceAccountToken.path == 'token' && (!has(s.serviceAccountToken.audience)) && s.serviceAccountToken.expirationSeconds <= 7200) || (has(s.configMap) && s.configMap.name == 'kube-root-ca.crt' && size(s.configMap.items) == 1 && s.configMap.items[0].key == 'ca.crt' && s.configMap.items[0].path == 'ca.crt') || (has(s.downwardAPI) && size(s.downwardAPI.items) == 1 && s.downwardAPI.items[0].path == 'namespace' && s.downwardAPI.items[0].fieldRef.fieldPath == 'metadata.namespace')) && p.volumes[0].projected.sources.exists(s, has(s.serviceAccountToken)) && p.volumes[0].projected.sources.exists(s, has(s.configMap)) && p.volumes[0].projected.sources.exists(s, has(s.downwardAPI))",
                       "has(p.containers[0].volumeMounts) && size(p.containers[0].volumeMounts) == 1 && p.containers[0].volumeMounts[0].name == p.volumes[0].name && p.containers[0].volumeMounts[0].mountPath == '/var/run/secrets/kubernetes.io/serviceaccount' && p.containers[0].volumeMounts[0].readOnly"]
        else:
            checks += ["(!has(p.volumes) || size(p.volumes) == 0)","(!has(p.containers[0].volumeMounts) || size(p.containers[0].volumeMounts) == 0)"]
        alternatives.append('('+' && '.join(checks)+')')
    return '('+' || '.join(alternatives)+')'


def documents(image):
    runtime = runtime_expression(image)
    final = runtime_expression(image, final=True)
    renewal = renewal_expression()
    renewal_pod = renewal_expression(final=True)
    # Lists bind p once, without relying on optional CEL language extensions.
    check = lambda path, rule: f'[{path}].all(p, {rule})'
    cluster = lambda path: f"has({path}.headGroupSpec) && (!has({path}.headGroupSpec.serviceType) || {path}.headGroupSpec.serviceType == '' || {path}.headGroupSpec.serviceType == 'ClusterIP') && (!has({path}.headGroupSpec.enableIngress) || !{path}.headGroupSpec.enableIngress) && !has({path}.headGroupSpec.headService) && {check(path+'.headGroupSpec.template.spec',runtime)} && (!has({path}.workerGroupSpecs) || {path}.workerGroupSpecs.all(w, {check('w.template.spec',runtime)}))"
    rules = [
        ('rayclusters', 'ray.io', cluster('object.spec')),
        ('rayjobs', 'ray.io', f"has(object.spec.submitterPodTemplate) && {check('object.spec.submitterPodTemplate.spec',runtime)} && ((has(object.spec.rayClusterSpec) && ({cluster('object.spec.rayClusterSpec')})) || (!has(object.spec.rayClusterSpec) && has(object.spec.clusterSelector) && size(object.spec.clusterSelector) == 2 && 'ray.io/cluster' in object.spec.clusterSelector && object.spec.clusterSelector['rayClusterNamespace'] == 'default'))"),
        ('jobs', 'batch', check('object.spec.template.spec',f'({runtime}) || ({renewal})')),
        ('cronjobs', 'batch', check('object.spec.jobTemplate.spec.template.spec',renewal)),
        ('pods', '', check('object.spec',f'({final}) || ({renewal_pod})')),
    ]
    result = []
    for resource, group, expression in rules:
        name = POLICY+'-'+resource
        result += [{'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicy',
                    'metadata':{'name':name},'spec':{'failurePolicy':'Fail',
                     'matchConstraints':{'resourceRules':[{'apiGroups':[group],'apiVersions':['*'],
                        'operations':['CREATE','UPDATE'],'resources':[resource]+(['pods/ephemeralcontainers'] if resource == 'pods' else [])}]},
                     'validations':[{'expression':expression,'message':'Michelangelo runtime containment rejected unsafe workload'}]}},
                   {'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicyBinding',
                    'metadata':{'name':name},'spec':{'policyName':name,'validationActions':['Deny'],
                     'matchResources':{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'default'}}}}}]
    return result


def harden_rbac(core):
    core.apply([{'apiVersion':'rbac.authorization.k8s.io/v1','kind':kind,
                'metadata':{'name':ACCOUNT,'namespace':'default'},**body} for kind,body in (
        ('Role',{'rules':[{'apiGroups':['ray.io'],'resources':['rayclusters','rayjobs'],'verbs':['get','list','watch','create','update','patch','delete']},
                         {'apiGroups':[''],'resources':['pods','services','events'],'verbs':['get','list','watch']},
                         {'apiGroups':[''],'resources':['pods/log'],'verbs':['get']}]}),
        ('RoleBinding',{'subjects':[{'kind':'ServiceAccount','name':ACCOUNT,'namespace':'default'}],
                        'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':ACCOUNT}}))])
    for kind in ('rolebinding','role'):
        core.run(['kubectl','-n',core.NS,'delete',kind,ACCOUNT,'--ignore-not-found=true','--wait=true','--timeout=60s'])


def install(core, images):
    harden_rbac(core)
    core.apply(documents(images['examples']['pinned']))
    verify_enforcement(core, images['examples']['pinned'])
    require_clean_runtime(core)
    core.run(['kubectl','delete','clusterpolicy','michelangelo-lifecycle-runtime-access','--ignore-not-found=true','--wait=true','--timeout=60s'])
    # Rollback retains these policies and RBAC; an old application image must
    # stay paused rather than reintroducing the old authorization boundary.
    core.save(core.WORK/'runtime-security.json', {'profile':2,'image':images['examples']['pinned'],
              'policies':[x['metadata']['name'] for x in documents(images['examples']['pinned']) if x['kind']=='ValidatingAdmissionPolicy']})


def verify_enforcement(core, image):
    """Require an actual admission denial, not a schema or RBAC error."""
    probe={'apiVersion':'v1','kind':'Pod','metadata':{'name':'ma-runtime-security-probe','namespace':'default'},
           'spec':{'serviceAccountName':RUNNER,'hostPID':True,'containers':[{'name':'probe','image':image}]}}
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        result=subprocess.run(['kubectl','create','--dry-run=server','-f','-'],input=json.dumps(probe),capture_output=True,text=True)
        if result.returncode and 'Michelangelo runtime containment rejected unsafe workload' in result.stderr:
            return
        time.sleep(2)
    raise RuntimeError('Runtime admission denial not verified; maintenance retained')


def require_clean_runtime(core):
    """A selector must never reach a cluster created before containment existed.

    Old native objects are not grandfathered or silently deleted. Drain and
    inspect them separately before retrying installation under maintenance.
    """
    native=json.loads(core.run(['kubectl','-n','default','get','rayclusters.ray.io,rayjobs.ray.io','-o','json'],capture=True))
    if native.get('items'):
        raise RuntimeError('Existing native Ray resources require drain and review before containment; maintenance retained')
    # Validate other surviving workloads, rather than trusting names/labels or
    # assuming that the old credential only used the scheduler's usual path.
    default=json.loads(core.run(['kubectl','-n','default','get','pods,jobs','-o','json'],capture=True))
    for item in default.get('items',[]):
        if terminal_workload(item):
            continue
        probe=copy.deepcopy(item)
        probe['metadata']={'name':'ma-preexisting-containment-probe','namespace':'default'}
        probe.pop('status',None)
        if probe['kind']=='Pod':probe['spec'].pop('nodeName',None)
        else:
            probe['spec'].pop('selector',None)
            probe['spec'].get('template',{}).get('metadata',{}).pop('labels',None)
        result=subprocess.run(['kubectl','create','--dry-run=server','-f','-'],input=json.dumps(probe),capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError('Preexisting default workload did not pass containment; maintenance retained')
    require_owned_platform_workloads(core)


def terminal_workload(item):
    status=item.get('status',{})
    if item['kind']=='Pod':return status.get('phase') in ('Succeeded','Failed')
    return any(c.get('type') in ('Complete','Failed') and c.get('status')=='True' for c in status.get('conditions',[]))


def execution_profile(spec):
    """Compare executable fields with the installed controller's full template.

    Only API-injected projected credentials and scheduling/default fields are
    normalized. Extra containers, mounts, credentials or commands stay visible.
    """
    spec=copy.deepcopy(spec)
    volumes=spec.get('volumes',[])
    injected={v['name'] for v in volumes if v['name'].startswith('kube-api-access-') and
              set(v)=={'name','projected'} and len(v['projected'].get('sources',[]))==3 and
              all(set(s) in ({'serviceAccountToken'},{'configMap'},{'downwardAPI'}) for s in v['projected']['sources'])}
    spec['volumes']=[v for v in volumes if v['name'] not in injected]
    for kind in ('containers','initContainers','ephemeralContainers'):
        for container in spec.get(kind,[]):
            container['volumeMounts']=[m for m in container.get('volumeMounts',[]) if not
                (m['name'] in injected and m.get('mountPath')=='/var/run/secrets/kubernetes.io/serviceaccount' and m.get('readOnly') is True)]
        # Container API defaults do not grant code execution or identity. Keep
        # every field that can alter executable behavior or credential access.
        fields=('name','image','command','args','env','envFrom','volumeMounts','volumeDevices','securityContext',
                'lifecycle','livenessProbe','readinessProbe','startupProbe','ports','workingDir')
        spec[kind]=[{key:c.get(key,[] if key in ('command','args','env','envFrom','volumeMounts','volumeDevices','ports') else None) for key in fields} for c in spec.get(kind,[])]
    spec.setdefault('serviceAccountName','default')
    keys=('containers','initContainers','ephemeralContainers','volumes','serviceAccountName','automountServiceAccountToken',
          'imagePullSecrets','securityContext','hostNetwork','hostPID','hostIPC','shareProcessNamespace','hostAliases','runtimeClassName')
    return {key:spec.get(key,True if key=='automountServiceAccountToken' else ([] if key in ('containers','initContainers','ephemeralContainers','volumes','imagePullSecrets','hostAliases') else False if key in ('hostNetwork','hostPID','hostIPC','shareProcessNamespace') else None)) for key in keys}


def require_owned_platform_workloads(core):
    import yaml
    manifest=list(yaml.safe_load_all(core.run(['helm','get','manifest',core.APP,'-n',core.NS],capture=True)))
    declared={(x.get('kind'),x.get('metadata',{}).get('name')):x for x in manifest if x and x.get('kind') in ('Deployment','StatefulSet','DaemonSet','Job')}
    declared.update({('Deployment',name):None for name in ('ma-identity','ma-browser-edge','ma-lifecycle-logs','ma-publication-controller')})
    inventory=json.loads(core.run(['kubectl','-n',core.NS,'get','pods,jobs,replicasets,deployments,statefulsets,daemonsets','-o','json'],capture=True))
    objects={x['metadata'].get('uid'):x for x in inventory.get('items',[]) if x['metadata'].get('uid')}
    def parent(item):
        refs=[r for r in item['metadata'].get('ownerReferences',[]) if r.get('controller')]
        if len(refs)!=1:return None
        value=objects.get(refs[0]['uid'])
        if value is None or value.get('kind')!=refs[0]['kind'] or value['metadata']['name']!=refs[0]['name']:return None
        return value
    for item in inventory.get('items',[]):
        if item['kind'] not in ('Pod','Job') or terminal_workload(item):continue
        controller=parent(item) if item['kind']=='Pod' else item
        if controller is None:raise RuntimeError('Unowned platform workload requires review; maintenance retained')
        root=parent(controller) if controller['kind']=='ReplicaSet' else controller
        if root is None or (root['kind'],root['metadata']['name']) not in declared:
            raise RuntimeError('Unrecognized platform workload controller; maintenance retained')
        intended=declared[(root['kind'],root['metadata']['name'])]
        if root['kind']=='Job' and intended is not None and execution_profile(root['spec']['template']['spec'])!=execution_profile(intended['spec']['template']['spec']):
            raise RuntimeError('Platform Job differs from its declared release template; maintenance retained')
        expected=controller['spec']['template']['spec']
        actual=item['spec'] if item['kind']=='Pod' else item['spec']['template']['spec']
        if execution_profile(actual)!=execution_profile(expected):
            raise RuntimeError('Platform workload differs from its installed controller template; maintenance retained')


def verify_manager_identity(core, server, ca, token, image):
    """Use the real bearer identity. Impersonation is unreliable with OKE IAM."""
    import ssl
    import urllib.request
    import urllib.error
    context=ssl.create_default_context(cadata=ca)
    def call(path,body):
        request=urllib.request.Request(server.rstrip('/')+path,data=json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,context=context,timeout=30) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.loads(error.read())
    checks=[]
    for namespace,group,resource,verb,allowed in (
        ('default','','pods','create',False),('default','batch','jobs','create',False),
        ('ma-system','','pods','create',False),('ma-system','ray.io','rayclusters','create',False),
        ('default','','secrets','get',False),('ma-system','','secrets','get',False),
        ('default','rbac.authorization.k8s.io','rolebindings','create',False),
        ('default','ray.io','rayclusters','create',True),('default','','pods','get',True)):
        code,result=call('/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
            {'apiVersion':'authorization.k8s.io/v1','kind':'SelfSubjectAccessReview','spec':{'resourceAttributes':{'namespace':namespace,'group':group,'resource':resource,'verb':verb}}})
        if code!=201 or result.get('status',{}).get('allowed') is not allowed or result['status'].get('evaluationError'):
            raise RuntimeError('Actual compute-manager permission review failed; maintenance retained')
        checks.append({'namespace':namespace,'resource':resource,'verb':verb,'allowed':allowed})
    pod={'serviceAccountName':RUNNER,'securityContext':{'runAsNonRoot':True,'runAsUser':65534,'fsGroup':65534,'seccompProfile':{'type':'RuntimeDefault'}},
         'containers':[{'name':'ray','image':image,'securityContext':{'allowPrivilegeEscalation':False,'runAsNonRoot':True,'capabilities':{'drop':['ALL']}},
                        'envFrom':[{'configMapRef':{'name':'ma-lifecycle-runtime-artifacts'}},{'secretRef':{'name':'ma-lifecycle-runtime-artifacts'}}]}],
         'imagePullSecrets':[{'name':'ma-lifecycle-runtime-registry'}],'volumes':[{'name':'ray','emptyDir':{}}]}
    cluster={'apiVersion':'ray.io/v1','kind':'RayCluster','metadata':{'name':'ma-runtime-identity-probe','namespace':'default'},
             'spec':{'rayVersion':'2.56.1','headGroupSpec':{'rayStartParams':{},'template':{'spec':pod}},
                     'workerGroupSpecs':[{'groupName':'worker','replicas':0,'minReplicas':0,'maxReplicas':1,'rayStartParams':{},'template':{'spec':copy.deepcopy(pod)}}]}}
    path='/apis/ray.io/v1/namespaces/default/rayclusters?dryRun=All'
    code,_=call(path,cluster)
    if code!=201:raise RuntimeError('Legitimate compute-manager RayCluster dry run failed; maintenance retained')
    cluster['spec']['workerGroupSpecs'][0]['template']['spec']['serviceAccountName']=ACCOUNT+'-renewer'
    code,result=call(path,cluster)
    if code!=403 or 'Michelangelo runtime containment rejected unsafe workload' not in result.get('message',''):
        raise RuntimeError('Actual compute-manager unsafe template denial failed; maintenance retained')
    core.save(core.WORK/'compute-containment-evidence.json',{'actual_bearer':True,'dry_run_only':True,'permissions':checks,
              'legitimate_ray_template':'passed','unsafe_worker_service_account':'denied','production_ready':False})
