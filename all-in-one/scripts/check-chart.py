#!/usr/bin/env python3
"""Render the OCI chart and TLS post-renderer with synthetic, credential-free inputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'installer'))
import install
sys.path.insert(0, str(ROOT / 'installer/transport'))
import mysql_router


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    args = parser.parse_args()
    chart = args.source.resolve() / 'helm/michelangelo'
    config = {'object_storage_namespace': 'example', 'region': 'us-chicago-1',
              'artifact_bucket': 'example-artifacts', 'registry': 'registry.example/release',
              'public_hostname': 'studio.example.com'}
    config.update({name + '_image': 'registry.example/' + name + ':candidate@sha256:' + 'b' * 64
                   for name in ('mysql_client', 'envoy', 'temporal_server', 'temporal_admin')})
    images = {name: {'pinned': 'registry.example/' + name + '@sha256:' + 'a' * 64}
              for name in ('apiserver', 'controllermgr', 'worker', 'ui', 'collector', 'triton')}
    with tempfile.TemporaryDirectory() as directory:
        # Use an empty repository configuration so this check also works on fresh runners.
        helm_env = {**os.environ, 'HELM_REPOSITORY_CONFIG': str(Path(directory) / 'repositories.yaml'),
                    'HELM_REPOSITORY_CACHE': str(Path(directory) / 'repository-cache')}
        for dependency in yaml.safe_load((chart / 'Chart.yaml').read_text()).get('dependencies', []):
            subprocess.run(['helm', 'repo', 'add', dependency['name'], dependency['repository']],
                           env=helm_env, check=True)
        subprocess.run(['helm', 'dependency', 'build', str(chart)], env=helm_env, check=True)
        values = Path(directory) / 'values.json'
        values.write_text(json.dumps(install.values(config, 'mysql.example.internal', images)))
        subprocess.run(['helm', 'lint', str(chart), '-f', str(values)], env=helm_env, check=True)
        rendered = subprocess.check_output(['helm', 'template', 'michelangelo', str(chart),
                                           '-n', 'ma-system', '-f', str(values)], env=helm_env, text=True)
    env = {**os.environ, 'MYSQL_ROUTER_IMAGE': install.ROUTER_IMAGE,
           'MYSQL_ROUTER_HOST': 'mysql.example.internal',
           'MYSQL_ROUTER_CA_SECRET': 'database-ca', 'MYSQL_ROUTER_AUTH_SECRET': 'router-auth'}
    protected = subprocess.check_output([sys.executable, '-B', str(ROOT / 'installer/transport/render.py')],
                                        input=rendered, text=True, env=env)
    documents = [doc for doc in mysql_router.load(protected) if doc]
    namespace_jobs = [d for d in documents if d.get('kind') == 'Job' and d.get('metadata', {}).get('name') == 'michelangelo-temporal-namespace-setup']
    if len(namespace_jobs) != 1:
        raise RuntimeError('Missing Temporal namespace setup job')
    registration = namespace_jobs[0]['spec']['template']['spec']['containers'][0]
    if registration['image'] != config['temporal_admin_image'] or ' operator namespace ' not in registration['command'][-1] or 'tctl ' in registration['command'][-1]:
        raise RuntimeError('Namespace setup does not use the reviewed admin image and supported CLI')
    envoy_config = next(d['data']['envoy.yaml'] for d in documents if d.get('kind') == 'ConfigMap' and 'envoy.yaml' in d.get('data', {}))
    if 'michelangelo.api.v2.EvaluationReportService' not in envoy_config:
        raise RuntimeError('Publication evaluation service is not transcoded')
    checked = set()
    for doc in documents:
        pod = doc.get('spec', {}).get('template', {}).get('spec', {})
        for container in pod.get('containers', []) + pod.get('initContainers', []):
            if container.get('image', '').startswith('registry.example/') and {'name': 'michelangelo-registry'} not in pod.get('imagePullSecrets', []):
                raise RuntimeError('Private image lacks registry pull credentials: ' + doc['metadata']['name'] + '/' + container['name'])
        if doc['kind'] == 'Service' and doc.get('spec', {}).get('type', 'ClusterIP') != 'ClusterIP':
            raise RuntimeError('Default OCI chart exposes a nonprivate service')
        if doc['kind'] != 'Deployment':
            continue
        component = doc['metadata']['name'].removeprefix('michelangelo-')
        for container in doc['spec']['template']['spec']['containers']:
            if component in ('apiserver', 'controllermgr', 'worker', 'ui'):
                if container['image'] != images[component]['pinned']:
                    raise RuntimeError('Application digest was modified by the chart')
                checked.add(component)
    if checked != {'apiserver', 'controllermgr', 'worker', 'ui'}:
        raise RuntimeError('Missing application deployments')
    print(json.dumps({'chart_and_tls_render': 'passed', 'resources': len(documents),
                      'application_images': sorted(checked), 'cloud_validation': False}))


if __name__ == '__main__':
    main()
