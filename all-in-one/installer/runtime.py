#!/usr/bin/env python3
"""Fresh-site scoped compute/serving registrations and renewable runtime tokens."""
import base64
import json
import re
import subprocess
import ssl
from pathlib import Path
import urllib.parse
import urllib.request
import yaml
import install as core


def metadata(name, namespace='default'):
    return {'name': name, 'namespace': namespace}


def prepare_log_service(installer=core):
    """Resolve the UI's log upstream before Helm waits for nginx readiness.

    The private Service can precede its reader pods. Those pods and their scoped
    credentials are installed later by install(), using the same exact Service.
    """
    template = installer.RELEASE / 'platform/releases/v0.11.0/log-service.example.json'
    services = [item for item in json.loads(template.read_text())['items']
                if item.get('kind') == 'Service']
    if len(services) != 1 or services[0]['metadata']['name'] != 'ma-lifecycle-logs':
        raise RuntimeError('Reviewed lifecycle log Service is missing or ambiguous')
    installer.apply(services)


def rbac(name, namespace, rules):
    return [{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': metadata(name, namespace), 'rules': rules},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': metadata(name, namespace),
             'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': 'default'}],
             'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name}}]


def token_registration(account, cluster_name, cluster_data, image):
    if not isinstance(image, str) or not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image):
        raise RuntimeError('Token-renewer image must be immutable')
    secret_name = 'cluster-' + cluster_name + '-client-token'
    token = core.run(['kubectl', '-n', 'default', 'create', 'token', account, '--duration=24h'], capture=True).strip()
    core.secret(secret_name, {'token': token}, 'default')
    core.secret('cluster-' + cluster_name + '-ca-data', {'cadata': cluster_data}, 'default')
    template = (core.RELEASE / 'platform/releases/v0.11.0/token-renewal.example.yaml').read_text()
    replacements = {'REPLACE_RENEWER_NAME': account + '-renewer', 'REPLACE_RUNTIME_NAMESPACE': 'default',
                    'REPLACE_RUNTIME_ACCOUNT': account, 'REPLACE_TOKEN_SECRET': secret_name,
                    'REPLACE_RENEWAL_JOB_NAME': account + '-token-renew', 'REPLACE_SYSTEM_NODE_LABEL': 'workload.oci.oracle.com/pool',
                    'REPLACE_SYSTEM_NODE_VALUE': 'system'}
    for old, new in replacements.items():
        template = template.replace(old, new)
    original_image = 'docker.io/library/python@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79'
    if template.count(original_image) != 1:
        raise RuntimeError('Reviewed token-renewer image is missing or mutable')
    template = template.replace(original_image, image)
    if re.search(r'\bREPLACE_[A-Z][A-Z0-9_]*\b', template):
        raise RuntimeError('Runtime renewal template is incomplete')
    documents = list(yaml.safe_load_all(template))
    for document in documents:
        if document.get('kind') == 'CronJob':
            document['spec']['jobTemplate']['spec']['template']['spec']['imagePullSecrets'] = [
                {'name': 'ma-lifecycle-runtime-registry'}]
    core.apply(documents)
    return token


def install(config, images):
    release = core.RELEASE / 'platform/releases/v0.11.0'
    permissions = json.loads((release / 'serving-permissions.example.json').read_text())['items']
    # Configuration examples have no secrets and define each dedicated project's
    # runtime rights. Instantiate them for the named fresh-site namespaces only.
    namespace_names = sorted({item.get('metadata', {}).get('namespace') for item in permissions
                              if item.get('metadata', {}).get('namespace')})
    namespace_names = ['ma-gateway' if name == 'ma-lifecycle-gateway' else name for name in namespace_names]
    core.apply([{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': name}} for name in namespace_names])
    permissions = json.loads(json.dumps(permissions).replace('ma-lifecycle-gateway', 'ma-gateway'))
    # Envoy Gateway's default data plane is in its controller namespace.
    # Keep only a single named Service read there; Gateway/HTTPRoutes remain
    # in ma-gateway and the model project namespaces respectively.
    for permission in permissions:
        if permission.get('kind') in ('Role', 'RoleBinding') and permission['metadata']['name'] == 'michelangelo-serving-private-gateway':
            permission['metadata']['namespace'] = 'envoy-gateway-system'
            if permission['kind'] == 'Role':
                for rule in permission['rules']:
                    if rule.get('resources') == ['services'] and rule.get('verbs') == ['get']:
                        rule['resourceNames'] = ['ma-gateway']
    core.apply(permissions)
    account = 'michelangelo-compute-manager'
    core.apply({'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata(account), 'automountServiceAccountToken': False})
    # Scheduler credentials only manage Ray resources in the reviewed runtime namespace.
    import workload_security
    workload_security.harden_rbac(core)
    kubeconfig = yaml.safe_load((core.WORK / 'kubeconfig').read_text())['clusters'][0]['cluster']
    ca = base64.b64decode(kubeconfig['certificate-authority-data']).decode()
    server = urllib.parse.urlsplit(kubeconfig['server'])
    runtime_tokens = {}
    for name, manager in [('oci-compute', account), ('oci-serving', 'michelangelo-serving-manager')]:
        runtime_tokens[manager] = token_registration(manager, name, ca, images['identity']['pinned'])
        # Serving targets carry their own explicit connection and credentials.
        # Register only compute with the generic Ray/Spark scheduler, whose
        # default selection otherwise includes the restricted serving account.
        if name != 'oci-compute':
            continue
        resource = {'apiVersion': 'michelangelo.api/v2', 'kind': 'Cluster', 'metadata': metadata(name, core.NS),
                    'spec': {'dc': 'DC_TYPE_CLOUD_OCI', 'sla': 'SLA_TYPE_EPHEMERAL', 'region': config['region'],
                             'kubernetes': {'rest': {'host': server.scheme + '://' + server.hostname, 'port': str(server.port or 443),
                                           'tokenTag': 'cluster-' + name + '-client-token', 'caDataTag': 'cluster-' + name + '-ca-data'}, 'skus': []}}}
        core.apply(resource)
    # Remove the exact obsolete installer-owned scheduler registration on
    # upgrade. Keep its serving connection Secrets and token renewer intact.
    core.run(['kubectl', '-n', core.NS, 'delete', 'clusters.michelangelo.api',
              'oci-serving', '--ignore-not-found=true', '--wait=true', '--timeout=60s'])
    # Upstream secret lookups intentionally use default; the chart service
    # account gets read access to those four registration names specifically.
    core.apply([{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': metadata('ma-runtime-client-secrets'),
        'rules': [{'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': ['cluster-' + name + '-' + suffix
                    for name in ('oci-compute', 'oci-serving') for suffix in ('client-token', 'ca-data')], 'verbs': ['get']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': metadata('ma-runtime-client-secrets'),
         'subjects': [{'kind': 'ServiceAccount', 'name': 'michelangelo', 'namespace': core.NS}],
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'ma-runtime-client-secrets'}}])
    storage_config = {'AWS_ENDPOINT_URL': 'https://' + config['object_storage_namespace'] + '.compat.objectstorage.' + config['region'] + '.oraclecloud.com',
                      # Botocore/s3fs reads AWS_DEFAULT_REGION; other runtimes use AWS_REGION.
                      'AWS_REGION': config['region'], 'AWS_DEFAULT_REGION': config['region'],
                      'AWS_S3_BUCKET': config['artifact_bucket'], 'AWS_EC2_METADATA_DISABLED': 'true',
                      # OCI S3 compatibility does not support AWS chunked checksum trailers.
                      'AWS_REQUEST_CHECKSUM_CALCULATION': 'when_required', 'AWS_RESPONSE_CHECKSUM_VALIDATION': 'when_required',
                      'MODEL_ARTIFACT_PREFIX': 'studio-demos/lifecycle/'}
    registry = core.run(['kubectl', '-n', core.NS, 'get', 'secret', 'michelangelo-registry', '-o', 'json'], capture=True)
    registry = json.loads(registry)['data']['.dockerconfigjson']
    for namespace in sorted(set(namespace_names + [core.NS, 'default'])):
        core.secret('ma-lifecycle-runtime-registry', {'.dockerconfigjson': base64.b64decode(registry).decode()}, namespace, 'kubernetes.io/dockerconfigjson')
        core.secret('michelangelo-runtime-artifacts', {'AWS_ACCESS_KEY_ID': config['runtime_artifact_access_key'], 'AWS_SECRET_ACCESS_KEY': config['runtime_artifact_secret_key']}, namespace)
        core.secret('ma-lifecycle-runtime-artifacts', {'AWS_ACCESS_KEY_ID': config['artifact_access_key'] if namespace == 'default' else config['runtime_artifact_access_key'], 'AWS_SECRET_ACCESS_KEY': config['artifact_secret_key'] if namespace == 'default' else config['runtime_artifact_secret_key']}, namespace)
        for name in ('michelangelo-runtime-artifacts', 'ma-lifecycle-runtime-artifacts'):
            core.apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(name, namespace), 'data': storage_config})
    core.apply({'apiVersion':'v1','kind':'ServiceAccount','metadata':metadata('michelangelo-lifecycle-runner'),
                'automountServiceAccountToken':True,'imagePullSecrets':[{'name':'ma-lifecycle-runtime-registry'}]})
    workload_security.install(core, images)
    workload_security.verify_manager_identity(core, kubeconfig['server'], ca, runtime_tokens[account], images['examples']['pinned'])
    logs = (release / 'log-service.example.json').read_text().replace('REPLACE_LOG_READER_IMAGE_DIGEST_URI', images['delivery']['pinned'])
    core.apply(json.loads(logs)['items'])
    core.run(['kubectl', '-n', core.NS, 'rollout', 'status', 'deployment/ma-lifecycle-logs', '--timeout=10m'])
    # Check each real runtime identity. OKE's IAM authorizer can retain the
    # original installer identity during kubectl impersonation, giving a
    # misleading can-i result. Never place bearer tokens in process arguments.
    context = ssl.create_default_context(cadata=ca)
    review = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
              'spec': {'resourceAttributes': {'namespace': 'kube-system', 'verb': 'get', 'group': '', 'resource': 'secrets'}}}
    for manager, token in runtime_tokens.items():
        request = urllib.request.Request(kubeconfig['server'].rstrip('/') + '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
            data=json.dumps(review).encode(), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            status = json.load(response)['status']
        if status.get('allowed') is not False or status.get('evaluationError'):
            raise RuntimeError('Actual runtime identity authorization failed system Secret denial acceptance')
    core.save(core.WORK / 'runtime-evidence.json', {'compute_cluster': 'oci-compute', 'serving_target_connection': 'oci-serving', 'scheduler_registrations': ['oci-compute'],
              'token_rotation': 'six-hour CronJobs with24-hour bootstrap tokens', 'runtime_namespaces': namespace_names,
              'system_secret_reads': 'denied via actual runtime bearer SelfSubjectAccessReview', 'workload_execution_qualified': False})
