import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('workload_security',ROOT/'installer/workload_security.py')
security=importlib.util.module_from_spec(spec);spec.loader.exec_module(security)
IMAGE='registry.example.com/examples@sha256:'+'a'*64
RENEWAL_IMAGE='registry.example.com/identity@sha256:'+'b'*64


def admission_cases():
    pod={'serviceAccountName':security.RUNNER,'securityContext':{'runAsNonRoot':True,'runAsUser':65534,'seccompProfile':{'type':'RuntimeDefault'}},
         'containers':[{'name':'worker','image':IMAGE,'securityContext':{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
                        'envFrom':[{'secretRef':{'name':'ma-lifecycle-runtime-artifacts'}}]}],
         'volumes':[{'name':'ray','emptyDir':{}}],'imagePullSecrets':[{'name':'ma-lifecycle-runtime-registry'}]}
    cluster={'headGroupSpec':{'template':{'spec':copy.deepcopy(pod)}},
             'workerGroupSpecs':[{'groupName':n,'template':{'spec':copy.deepcopy(pod)}} for n in ('first','second')]}
    expressions={x['metadata']['name'].removeprefix(security.POLICY+'-'):x['spec']['validations'][0]['expression'] for x in security.documents(IMAGE, RENEWAL_IMAGE) if x['kind']=='ValidatingAdmissionPolicy'}
    cases=[]
    def add(name,resource,value,allowed):cases.append({'Name':name,'Expression':expressions[resource],'Object':{'spec':value},'Allowed':allowed})
    add('valid cluster','rayclusters',copy.deepcopy(cluster),True)
    external=copy.deepcopy(cluster);external['headGroupSpec']['serviceType']='LoadBalancer';add('public head service','rayclusters',external,False)
    external=copy.deepcopy(cluster);external['headGroupSpec']['headService']={'spec':{'type':'LoadBalancer'}};add('custom head service','rayclusters',external,False)
    add('valid pod','pods',copy.deepcopy(pod),True)
    scheduled=copy.deepcopy(pod);scheduled['nodeName']='worker-1'
    add('scheduled pod metadata update','pods',scheduled,True);cases[-1]['Operation']='UPDATE'
    add('direct node selection','pods',copy.deepcopy(scheduled),False)
    job={'submitterPodTemplate':{'spec':copy.deepcopy(pod)},'clusterSelector':{'ray.io/cluster':'any-name','rayClusterNamespace':'default'}}
    add('valid selector RayJob','rayjobs',copy.deepcopy(job),True)
    inline={'submitterPodTemplate':{'spec':copy.deepcopy(pod)},'rayClusterSpec':copy.deepcopy(cluster)}
    add('valid inline RayJob','rayjobs',copy.deepcopy(inline),True)
    changes=[('service account',lambda p:p.update(serviceAccountName='michelangelo-compute-manager-renewer')),
             ('image',lambda p:p['containers'][0].update(image='attacker:latest')),
             ('hostPath',lambda p:p['volumes'].append({'name':'host','hostPath':{'path':'/'}})),
             ('Secret volume',lambda p:p['volumes'].append({'name':'secret','secret':{'secretName':'cluster-oci-compute-client-token'}})),
             ('projected Secret',lambda p:p['volumes'].append({'name':'secret','projected':{'sources':[{'secret':{'name':'secret'}}]}})),
             ('manual token',lambda p:p['volumes'].append({'name':'token','projected':{'sources':[{'serviceAccountToken':{'path':'token'}}]}})),
             ('CSI',lambda p:p['volumes'].append({'name':'csi','csi':{'driver':'attacker','nodePublishSecretRef':{'name':'secret'}}})),
             ('envFrom',lambda p:p['containers'][0]['envFrom'].append({'secretRef':{'name':'michelangelo-registry'}})),
             ('env key',lambda p:p['containers'][0].update(env=[{'name':'TOKEN','valueFrom':{'secretKeyRef':{'name':'cluster-oci-compute-client-token','key':'token'}}}])) ,
             ('init',lambda p:p.update(initContainers=[{'name':'evil','image':'attacker'}])),
             ('sidecar',lambda p:p['containers'].append({'name':'evil','image':'attacker'})),
             ('ephemeral',lambda p:p.update(ephemeralContainers=[{'name':'evil','image':'attacker'}])),
             ('privileged',lambda p:p['containers'][0]['securityContext'].update(privileged=True)),
             ('capability',lambda p:p['containers'][0]['securityContext']['capabilities'].update(add=['SYS_ADMIN'])),
             ('host network',lambda p:p.update(hostNetwork=True))]
    for label,change in changes:
        value=copy.deepcopy(pod);change(value);add(label+' final pod','pods',value,False)
        value=copy.deepcopy(cluster);change(value['workerGroupSpecs'][1]['template']['spec']);add(label+' second worker','rayclusters',value,False)
        value=copy.deepcopy(inline);change(value['rayClusterSpec']['headGroupSpec']['template']['spec']);add(label+' inline','rayjobs',value,False)
        value=copy.deepcopy(job);change(value['submitterPodTemplate']['spec']);add(label+' submitter','rayjobs',value,False)
    add('missing submitter','rayjobs',{'clusterSelector':job['clusterSelector']},False)
    add('foreign selector','rayjobs',{**job,'clusterSelector':{'ray.io/cluster':'name','rayClusterNamespace':'ma-system'}},False)
    add('valid batch job','jobs',{'template':{'spec':copy.deepcopy(pod)}},True)
    import yaml
    text=(ROOT/'installer/release/platform/releases/v0.11.0/token-renewal.example.yaml').read_text()
    for short in ('compute','serving'):
        account='michelangelo-'+short+'-manager'
        rendered=text
        for old,new in {'REPLACE_RENEWER_NAME':account+'-renewer','REPLACE_RUNTIME_NAMESPACE':'default','REPLACE_RUNTIME_ACCOUNT':account,
                        'REPLACE_TOKEN_SECRET':'cluster-oci-'+short+'-client-token','REPLACE_RENEWAL_JOB_NAME':account+'-token-renew',
                        'REPLACE_SYSTEM_NODE_LABEL':'workload.oci.oracle.com/pool','REPLACE_SYSTEM_NODE_VALUE':'system'}.items():rendered=rendered.replace(old,new)
        cron=list(yaml.safe_load_all(rendered))[-1]
        renewal_pod=cron['spec']['jobTemplate']['spec']['template']['spec']
        legacy_image=renewal_pod['containers'][0]['image']
        renewal_pod['containers'][0]['image']=RENEWAL_IMAGE
        renewal_pod['imagePullSecrets']=[{'name':'ma-lifecycle-runtime-registry'}]
        bad=copy.deepcopy(cron['spec']);bad['jobTemplate']['spec']['template']['spec']['containers'][0]['image']=legacy_image
        add(short+' obsolete renewal image','cronjobs',bad,False)
        bad=copy.deepcopy(cron['spec']);bad['jobTemplate']['spec']['template']['spec']['imagePullSecrets']=[{'name':'unapproved-registry'}]
        add(short+' unapproved renewal registry','cronjobs',bad,False)
        add(short+' renewal CronJob','cronjobs',cron['spec'],True)
        add(short+' renewal Job','jobs',cron['spec']['jobTemplate']['spec'],True)
        bad=copy.deepcopy(cron['spec']);bad['jobTemplate']['spec']['template']['spec']['containers'][0]['args']=['steal token']
        add(short+' substituted renewal program','cronjobs',bad,False)
        bad=copy.deepcopy(cron['spec']);bad['jobTemplate']['spec']['template']['spec']['containers'][0]['lifecycle']={'postStart':{'exec':{'command':['steal-token']}}}
        add(short+' renewal lifecycle hook','cronjobs',bad,False)
        bad=copy.deepcopy(cron['spec']);bad['jobTemplate']['spec']['template']['spec']['containers'][0]['securityContext']['procMount']='Unmasked'
        add(short+' renewal unmasked proc','cronjobs',bad,False)
    return cases


class WorkloadSecurityTests(unittest.TestCase):
    def test_manager_has_no_pod_job_or_platform_mutations(self):
        applied=[];commands=[]
        security.harden_rbac(types.SimpleNamespace(NS='ma-system',apply=applied.append,run=commands.append))
        role=applied[0][0]
        self.assertEqual(role['metadata']['namespace'],'default')
        for rule in role['rules']:
            if rule['apiGroups']!=['ray.io']:self.assertFalse(set(rule['verbs']) & {'create','update','patch','delete'})
        self.assertEqual({x[4] for x in commands},{'rolebinding','role'})
        self.assertTrue(all('ma-system' in x for x in commands))

    def test_preexisting_native_cluster_cannot_be_targeted_after_upgrade(self):
        core=types.SimpleNamespace(run=lambda *a,**k:json.dumps({'items':[{'kind':'RayCluster'}]}))
        with self.assertRaisesRegex(RuntimeError,'Existing native Ray'):security.require_clean_runtime(core)
        security.require_clean_runtime(types.SimpleNamespace(NS='ma-system',APP='michelangelo',run=lambda args,**k:'' if args[0]=='helm' else json.dumps({'items':[]})))

    def test_old_direct_platform_pods_and_jobs_block_containment(self):
        for item in ({'kind':'Pod','metadata':{'name':'evil','uid':'pod'},'spec':{'containers':[{'image':'evil'}]},'status':{'phase':'Running'}},
                     {'kind':'Job','metadata':{'name':'evil','uid':'job'},'spec':{'template':{'spec':{'containers':[{'image':'evil'}]}}}}):
            core=types.SimpleNamespace(NS='ma-system',APP='michelangelo',run=lambda args,**k:'' if args[0]=='helm' else json.dumps({'items':[item]}))
            with self.assertRaisesRegex(RuntimeError,'platform workload'):security.require_owned_platform_workloads(core)

    def test_platform_owner_reference_does_not_hide_changed_executable(self):
        template={'containers':[{'name':'identity','image':'approved@sha256:'+'a'*64}]}
        deployment={'kind':'Deployment','metadata':{'name':'ma-identity','uid':'deployment'},'spec':{'template':{'spec':copy.deepcopy(template)}}}
        replica={'kind':'ReplicaSet','metadata':{'name':'identity-rs','uid':'replica','ownerReferences':[{'controller':True,'uid':'deployment','kind':'Deployment','name':'ma-identity'}]},'spec':{'template':{'spec':copy.deepcopy(template)}}}
        pod={'kind':'Pod','metadata':{'name':'identity-pod','uid':'pod','ownerReferences':[{'controller':True,'uid':'replica','kind':'ReplicaSet','name':'identity-rs'}]},'spec':copy.deepcopy(template),'status':{'phase':'Running'}}
        core=types.SimpleNamespace(NS='ma-system',APP='michelangelo',run=lambda args,**k:'' if args[0]=='helm' else json.dumps({'items':[deployment,replica,pod]}))
        security.require_owned_platform_workloads(core)
        for change in (lambda spec:spec['containers'][0].update(image='unapproved:latest'),
                       lambda spec:spec.update(dnsPolicy='None',dnsConfig={'nameservers':['192.0.2.1']}),
                       lambda spec:spec['containers'][0].update(terminationMessagePath='/custom-path')):
            pod['spec']=copy.deepcopy(template);change(pod['spec'])
            with self.assertRaisesRegex(RuntimeError,'differs'):security.require_owned_platform_workloads(core)

    def test_full_platform_template_keeps_network_and_container_fields(self):
        template={'containers':[{'name':'api','image':IMAGE}]}
        for change in (
            lambda p:p.update(dnsPolicy='None',dnsConfig={'nameservers':['192.0.2.1']}),
            lambda p:p.update(hostname='alternate',subdomain='alternate'),
            lambda p:p.update(enableServiceLinks=False),
            lambda p:p.update(futurePodField={'enabled':True}),
            lambda p:p['containers'][0].update(terminationMessagePath='/custom-path'),
            lambda p:p['containers'][0].update(restartPolicy='Always'),
        ):
            pod=copy.deepcopy(template);change(pod)
            self.assertNotEqual(security.execution_profile(template),security.execution_profile(pod))

    def test_platform_template_tolerates_only_known_api_defaults(self):
        template={'containers':[{'name':'api','image':IMAGE}]}
        pod=copy.deepcopy(template);pod.update(nodeName='scheduled-worker',dnsPolicy='ClusterFirst',
            restartPolicy='Always',schedulerName='default-scheduler',terminationGracePeriodSeconds=30,
            enableServiceLinks=True,preemptionPolicy='PreemptLowerPriority',priority=0,
            serviceAccountName='default',serviceAccount='default',securityContext={},
            tolerations=[{'key':key,'operator':'Exists','effect':'NoExecute','tolerationSeconds':300}
                         for key in ('node.kubernetes.io/not-ready','node.kubernetes.io/unreachable')])
        pod['containers'][0].update(imagePullPolicy='IfNotPresent',terminationMessagePath='/dev/termination-log',
                                   terminationMessagePolicy='File',resources={})
        self.assertEqual(security.execution_profile(template),security.execution_profile(pod))
        pod['tolerations'][0]['tolerationSeconds']=3600
        self.assertNotEqual(security.execution_profile(template),security.execution_profile(pod))

    def test_volume_and_probe_defaults_preserve_security_relevant_changes(self):
        template={'containers':[{'name':'api','image':IMAGE,
            'startupProbe':{'exec':{'command':['/bin/check']},'failureThreshold':60}}],
            'volumes':[{'name':'config','configMap':{'name':'approved'}},
                       {'name':'credentials','secret':{'secretName':'approved','defaultMode':256}}]}
        actual=copy.deepcopy(template)
        actual['volumes'][0]['configMap']['defaultMode']=420
        actual['containers'][0]['startupProbe'].update(timeoutSeconds=1,periodSeconds=10,successThreshold=1)
        self.assertEqual(security.execution_profile(template),security.execution_profile(actual))
        for change in (
            lambda p:p['volumes'][0]['configMap'].update(defaultMode=511),
            lambda p:p['volumes'][0]['configMap'].update(name='unapproved'),
            lambda p:p['volumes'][1]['secret'].update(defaultMode=420),
            lambda p:p['containers'][0]['startupProbe'].update(timeoutSeconds=60),
            lambda p:p['containers'][0]['startupProbe']['exec'].update(command=['/bin/other']),
        ):
            modified=copy.deepcopy(actual);change(modified)
            self.assertNotEqual(security.execution_profile(template),security.execution_profile(modified))

    def test_only_standard_bounded_api_projection_is_normalized(self):
        template={'containers':[{'name':'api','image':IMAGE}]}
        pod=copy.deepcopy(template);pod['volumes']=[{'name':'kube-api-access-sample','projected':{
            'defaultMode':420,'sources':[{'serviceAccountToken':{'path':'token','expirationSeconds':3607}},
            {'configMap':{'name':'kube-root-ca.crt','items':[{'key':'ca.crt','path':'ca.crt'}]}},
            {'downwardAPI':{'items':[{'path':'namespace','fieldRef':{'apiVersion':'v1','fieldPath':'metadata.namespace'}}]}}]}}]
        pod['containers'][0]['volumeMounts']=[{'name':'kube-api-access-sample',
            'readOnly':True,'mountPath':'/var/run/secrets/kubernetes.io/serviceaccount'}]
        self.assertEqual(security.execution_profile(template),security.execution_profile(pod))
        for change in (lambda projected:projected['sources'][0]['serviceAccountToken'].update(audience='unapproved'),
                       lambda projected:projected['sources'][0]['serviceAccountToken'].update(expirationSeconds=86400),
                       lambda projected:projected['sources'][1]['configMap'].update(name='unapproved'),
                       lambda projected:projected['sources'][2]['downwardAPI']['items'][0]['fieldRef'].update(fieldPath='metadata.name')):
            modified=copy.deepcopy(pod);change(modified['volumes'][0]['projected'])
            self.assertNotEqual(security.execution_profile(template),security.execution_profile(modified))

    def test_all_policies_fail_closed_and_have_no_caller_exemption(self):
        for policy in security.documents(IMAGE, RENEWAL_IMAGE):
            if policy['kind']=='ValidatingAdmissionPolicy':
                self.assertEqual(policy['spec']['failurePolicy'],'Fail')
                self.assertEqual(policy['spec']['validations'][0]['reason'],'Forbidden')
                self.assertNotIn('matchConditions',policy['spec'])
            else:self.assertEqual(policy['spec']['validationActions'],['Deny'])

    @unittest.skipUnless(os.environ.get('MA_REVIEW_SOURCE_DIR') and os.environ.get('GO'), 'run check-source.py for real CEL evaluation')
    def test_exact_native_admission_expressions(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs=Path(directory)/'cases.json';inputs.write_text(json.dumps(admission_cases()))
            subprocess.run([os.environ['GO'],'run','-mod=mod',str(ROOT/'tests/check_workload_admission.go'),str(inputs)],
                           cwd=Path(os.environ['MA_REVIEW_SOURCE_DIR'])/'go',check=True)


if __name__=='__main__':unittest.main()
