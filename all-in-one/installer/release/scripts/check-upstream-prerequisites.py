#!/usr/bin/env python3
"""Read-only configuration/cluster preflight, not connectivity or site acceptance."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import yaml

DIGEST = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}')
CRDS = {'rayclusters.ray.io', 'rayjobs.ray.io', 'gateways.gateway.networking.k8s.io',
        'httproutes.gateway.networking.k8s.io', 'clusterpolicies.kyverno.io'}


def required(value, message):
    if not value:
        raise ValueError(message)


def pinned(image, label):
    required(isinstance(image, str) and DIGEST.fullmatch(image), 'Supply immutable image digest: ' + label)


def validate(values):
    required(isinstance(values, dict), 'Values must be a mapping')
    required('REPLACE_' not in json.dumps(values), 'Replace all site configuration placeholders')
    db, storage = values.get('metadataStorage', {}), values.get('objectStorage', {})
    required(not any(db.get(k) for k in ('rootPassword', 'password', 'adminPassword'))
             and not any(storage.get(k) for k in ('accessKeyId', 'secretAccessKey')),
             'Use existing Secret references instead of inline credentials')
    for section, keys in [('metadataStorage', ('host', 'database', 'user', 'existingSecret')),
                          ('objectStorage', ('endpoint', 'region', 'bucket', 'existingSecret')),
                          ('workflow', ('endpoint', 'domain'))]:
        for key in keys:
            required(values.get(section, {}).get(key), section + '.' + key + ' is required')
    required(storage.get('secure') is True and '://' not in storage['endpoint'],
             'Object Storage requires a hostname without scheme and secure=true')
    required(values.get('workflow', {}).get('engine') == 'temporal', 'This operator profile requires Temporal')
    for component in ('apiserver', 'envoy', 'ui'):
        v = values.get(component, {})
        required(v.get('service', {}).get('type', 'ClusterIP') == 'ClusterIP'
                 and not v.get('ingress', {}).get('enabled', False), 'Pilot services must remain private: ' + component)
    required(values.get('ui', {}).get('basicAuthSecret'), 'Private pilot login Secret is required')
    for component in ('apiserver', 'controllermgr', 'worker', 'ui'):
        image = values.get('images', {}).get(component, {})
        pinned(image.get('repository'), component)
        required(not image.get('tag'), 'Digest images must have an empty tag: ' + component)
    for name, image in [('envoy', values.get('images', {}).get('envoy')),
                        ('collector', values.get('images', {}).get('kuberayCollector')),
                        ('private login', values.get('ui', {}).get('authImage')),
                        ('schema initialization', db.get('initImage')),
                        ('Triton', values.get('inferenceServer', {}).get('triton', {}).get('defaultImage'))]:
        pinned(image, name)
    gateway = values.get('inferenceServer', {}).get('gateway', {})
    for key in ('name', 'serviceName', 'serviceNamespace', 'portName'):
        required(gateway.get(key), 'inferenceServer.gateway.' + key + ' is required')


def validate_runtime(runtime, values, namespace):
    required(namespace == 'ma-system', 'Retained pilot lease profile requires namespace ma-system')
    required(runtime.get('kind') == 'ConfigMap', 'Runtime configuration must be a ConfigMap')
    required(runtime.get('metadata', {}).get('namespace') == namespace, 'Runtime ConfigMap namespace must match --namespace')
    name = runtime.get('metadata', {}).get('name')
    references = values.get('controllermgr', {}).get('extraEnvFrom', [])
    required(name and any(r.get('configMapRef', {}).get('name') == name for r in references),
             'Controller must reference the supplied runtime ConfigMap by name')
    data = runtime.get('data', {})
    required('REPLACE_' not in json.dumps(data), 'Replace all runtime configuration placeholders')
    pinned(data.get('MA_MODEL_DELIVERY_IMAGE'), 'model delivery')
    for key in ('MA_MODEL_STORAGE_SECRET', 'MA_MODEL_STORAGE_CONFIG'):
        required(data.get(key), key + ' is required')
    required(data.get('MA_GATEWAY_NAMESPACE') == values['inferenceServer']['gateway']['serviceNamespace'],
             'Runtime and Helm gateway namespaces must match')
    required(data.get('MA_GATEWAY_CLUSTER_IP') == 'true', 'Runtime gateway must use ClusterIP')
    selector = json.loads(data.get('MA_PILOT_NODE_SELECTOR', '{}'))
    required(isinstance(selector, dict) and selector and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in selector.items()),
             'CPU selector must be a nonempty string mapping')
    tolerations = json.loads(data.get('MA_PILOT_NODE_TOLERATIONS', '[]'))
    required(isinstance(tolerations, list) and all(isinstance(t, dict) for t in tolerations), 'CPU tolerations must be a list of objects')
    return selector, tolerations


def flatten(objects):
    for obj in objects:
        if obj:
            if obj.get('kind') == 'List':
                yield from flatten(obj.get('items', []))
            else:
                yield obj


def pod_specs(obj):
    if obj.get('kind') == 'Pod':
        yield obj['spec']
    elif obj.get('kind') == 'CronJob':
        yield obj['spec']['jobTemplate']['spec']['template']['spec']
    elif 'template' in obj.get('spec', {}):
        yield obj['spec']['template']['spec']


def references(objects, namespace):
    """Return (kind, namespace, name, required-key) without reading credentials."""
    refs = set()
    for obj in objects:
        ns = obj.get('metadata', {}).get('namespace', namespace)
        for spec in pod_specs(obj):
            if spec.get('serviceAccountName'):
                refs.add(('ServiceAccount', ns, spec['serviceAccountName'], ''))
            for c in spec.get('containers', []) + spec.get('initContainers', []):
                pinned(c.get('image'), 'rendered container ' + c.get('name', 'unnamed'))
                for env in c.get('env', []):
                    for key, kind in [('secretKeyRef', 'Secret'), ('configMapKeyRef', 'ConfigMap')]:
                        ref = env.get('valueFrom', {}).get(key)
                        if ref and not ref.get('optional', False):
                            refs.add((kind, ns, ref['name'], ref['key']))
                for env in c.get('envFrom', []):
                    for key, kind in [('secretRef', 'Secret'), ('configMapRef', 'ConfigMap')]:
                        ref = env.get(key)
                        if ref and not ref.get('optional', False):
                            refs.add((kind, ns, ref['name'], ''))
            for ref in spec.get('imagePullSecrets', []):
                refs.add(('Secret', ns, ref['name'], '.dockerconfigjson'))
            for vol in spec.get('volumes', []):
                sources = [vol] + vol.get('projected', {}).get('sources', [])
                for source in sources:
                    for field, kind in [('secret', 'Secret'), ('configMap', 'ConfigMap')]:
                        ref = source.get(field)
                        if ref and not ref.get('optional', False):
                            name = ref.get('secretName', ref.get('name'))
                            for key in [i['key'] for i in ref.get('items', [])] or ['']:
                                refs.add((kind, ns, name, key))
    return refs


def private_services(objects):
    for obj in objects:
        spec = obj.get('spec', {})
        if obj.get('kind') == 'Service':
            required(spec.get('type', 'ClusterIP') == 'ClusterIP' and not spec.get('externalIPs'), 'Rendered services must remain private ClusterIP services')
        required(obj.get('kind') != 'Ingress', 'Ingress is not permitted in the private operator profile')


def run(command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.PIPE, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Helm/kubectl diagnostics may contain rendered configuration; never echo them.
        raise ValueError('Read-only command failed or timed out: ' + command[0] + '; check tool access and named prerequisites') from exc


class Cluster:
    def __init__(self, kubeconfig):
        self.command = ['kubectl', '--kubeconfig', str(kubeconfig), '--request-timeout=20s']

    def get(self, kind, name='', namespace=None):
        return json.loads(run(self.command + (['-n', namespace] if namespace else []) + ['get', kind] + ([name] if name else []) + ['-o', 'json']))

    def keys(self, kind, name, namespace):
        template = '{{range $k,$v := .data}}{{$k}}{{"\\n"}}{{end}}'
        if kind == 'ConfigMap':
            template += '{{range $k,$v := .binaryData}}{{$k}}{{"\\n"}}{{end}}'
        return set(run(self.command + ['-n', namespace, 'get', kind, name, '-o', 'go-template=' + template]).splitlines())


def eligible_nodes(nodes, selector, tolerations):
    def tolerates(taint):
        return any((not t.get('effect') or t['effect'] == taint.get('effect'))
                   and ((t.get('operator', 'Equal') == 'Exists' and (not t.get('key') or t['key'] == taint['key']))
                        or (t.get('operator', 'Equal') == 'Equal' and t.get('key') == taint['key'] and t.get('value', '') == taint.get('value', '')))
                   for t in tolerations)
    return [n for n in nodes if not n.get('spec', {}).get('unschedulable', False)
            and all(n.get('metadata', {}).get('labels', {}).get(k) == v for k, v in selector.items())
            and any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in n.get('status', {}).get('conditions', []))
            and all(tolerates(t) for t in n.get('spec', {}).get('taints', []) if t.get('effect') in ('NoSchedule', 'NoExecute'))]


def check_cluster(client, objects, runtime, values, namespace, selector, tolerations):
    refs = references(objects, namespace)
    # Chart-managed resources need not exist before the first Helm install.
    local = {(o['kind'], o.get('metadata', {}).get('namespace', namespace), o['metadata']['name']): o for o in objects}
    namespaces = {namespace} | {r[1] for r in refs} | {values['inferenceServer']['gateway']['serviceNamespace']}
    for ns in sorted(namespaces):
        client.get('namespace', ns)
    for kind, ns, name, key in sorted(refs):
        obj = local.get((kind, ns, name))
        if obj:
            keys = set(obj.get('data', {})) | set(obj.get('binaryData', {})) | set(obj.get('stringData', {}))
        elif kind in ('Secret', 'ConfigMap'):
            keys = client.keys(kind, name, ns)
        else:
            client.get(kind, name, ns)
            keys = set()
        required(not key or key in keys, 'Required key missing: ' + kind + ' ' + ns + '/' + name + '/' + key)
    current = client.get('configmap', runtime['metadata']['name'], namespace)
    required(all(current.get('data', {}).get(k) == v for k, v in runtime['data'].items()), 'Installed runtime ConfigMap does not match supplied site configuration')
    crds = {c['metadata']['name'] for c in client.get('crds')['items']}
    required(not CRDS - crds, 'Missing prerequisite CRDs: ' + ','.join(sorted(CRDS - crds)))
    for name in ('michelangelo-pilot-serving', 'michelangelo-pilot-workflows'):
        client.get('leases.coordination.k8s.io', name, namespace)
    gateway = values['inferenceServer']['gateway']
    gw = client.get('gateways.gateway.networking.k8s.io', gateway['name'], gateway['serviceNamespace'])
    required(any(l.get('name') == gateway['portName'] for l in gw.get('spec', {}).get('listeners', [])), 'Gateway listener name does not match Helm portName')
    service = client.get('service', gateway['serviceName'], gateway['serviceNamespace'])
    private_services([service])
    required(any(p.get('name') == gateway['portName'] for p in service.get('spec', {}).get('ports', [])), 'Gateway Service port name does not match Helm portName')
    nodes = eligible_nodes(client.get('nodes')['items'], selector, tolerations)
    required(nodes, 'No Ready, uncordoned CPU node matches the runtime selector and tolerations')
    return {'referenced_resources_present': True, 'prerequisite_crds_present': True, 'cpu_placement_eligible_nodes': len(nodes)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--values', type=Path, required=True)
    p.add_argument('--runtime-config', type=Path, required=True)
    p.add_argument('--namespace', default='ma-system')
    p.add_argument('--cluster', action='store_true')
    p.add_argument('--kubeconfig', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.cluster and (not a.kubeconfig or not a.kubeconfig.is_file()):
        p.error('--cluster requires an explicit existing --kubeconfig file')
    result = {'configuration_valid': False, 'helm_render_valid': False, 'cluster_checked': False,
              'connectivity_checked': False, 'lifecycle_acceptance': False, 'production_ready': False}
    try:
        values = yaml.safe_load(a.values.read_text())
        runtime = yaml.safe_load(a.runtime_config.read_text())
        validate(values)
        selector, tolerations = validate_runtime(runtime, values, a.namespace)
        result['configuration_valid'] = True
        chart = a.source / 'helm/michelangelo'
        run(['helm', 'lint', str(chart), '-f', str(a.values)])
        rendered = run(['helm', 'template', 'michelangelo', str(chart), '--skip-tests', '-n', a.namespace, '-f', str(a.values)])
        objects = list(flatten(yaml.safe_load_all(rendered)))
        private_services(objects)
        refs = references(objects, a.namespace)
        result.update(helm_render_valid=True, secret_reference_count=sum(r[0] == 'Secret' for r in refs),
                      configmap_reference_count=sum(r[0] == 'ConfigMap' for r in refs))
        if a.cluster:
            result.update(check_cluster(Cluster(a.kubeconfig), objects, runtime, values, a.namespace, selector, tolerations))
            result['cluster_checked'] = True
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as exc:
        # Do not serialize input/parser exceptions: they may include site values.
        result['error'] = str(exc) if type(exc) is ValueError else 'Invalid input or unreadable configuration; check the supplied files'
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if 'error' in result:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
