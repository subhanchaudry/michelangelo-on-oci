"""Install a bounded publication reconciler using read-only artifact credentials."""
import json
import hashlib
from pathlib import Path


def install(core, images):
    names = sorted({m['project'] for m in json.loads((core.RELEASE/'platform/serving/lifecycle/catalog.json').read_text())['models']})
    name = 'ma-publication-controller'
    labels = {'app.kubernetes.io/name': name}
    code = (Path(__file__).parent/'qualification/publication.py').read_text()
    core.apply([
        {'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':name,'namespace':core.NS}},
        {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name,'namespace':core.NS},
         'data':{'publication.py':code}},
        {'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':name,'namespace':core.NS},
         'spec':{'replicas':1,'selector':{'matchLabels':labels},'template':{'metadata':{'labels':labels,'annotations':{'checksum/publication-code':hashlib.sha256(code.encode()).hexdigest()}},'spec':{
             'serviceAccountName':name,'imagePullSecrets':[{'name':'michelangelo-registry'}],
             'nodeSelector':{'workload.oci.oracle.com/pool':'system'},
             'containers':[{'name':'reconciler','image':images['examples']['pinned'],
                'command':['python3','/qualification/publication.py'],
                'env':[{'name':'MA_API_HTTP','value':'http://michelangelo-envoy:8081'},
                       {'name':'MA_PUBLICATION_NAMESPACES','value':json.dumps(names)}],
                'envFrom':[{'configMapRef':{'name':'michelangelo-runtime-artifacts'}},
                           {'secretRef':{'name':'michelangelo-runtime-artifacts'}}],
                'resources':{'requests':{'cpu':'50m','memory':'128Mi'},'limits':{'cpu':'500m','memory':'1536Mi'}},
                'securityContext':{'runAsNonRoot':True,'runAsUser':65534,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
                'volumeMounts':[{'name':'code','mountPath':'/qualification','readOnly':True},{'name':'tmp','mountPath':'/tmp'}]}],
             'volumes':[{'name':'code','configMap':{'name':name}},{'name':'tmp','emptyDir':{}}]}}}},
    ])
    core.run(['kubectl','-n',core.NS,'rollout','status','deployment/'+name,'--timeout=5m'])
