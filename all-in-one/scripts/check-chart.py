#!/usr/bin/env python3
"""Render the OCI chart and TLS post-renderer with synthetic, credential-free inputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

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
    subprocess.run(['helm', 'dependency', 'build', str(chart)], check=True)
    config = {'object_storage_namespace': 'example', 'region': 'us-chicago-1',
              'artifact_bucket': 'example-artifacts', 'registry': 'registry.example/release',
              'public_hostname': 'studio.example.com'}
    images = {name: {'pinned': 'registry.example/' + name + '@sha256:' + 'a' * 64}
              for name in ('apiserver', 'controllermgr', 'worker', 'ui', 'collector', 'triton')}
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / 'values.json'
        values.write_text(json.dumps(install.values(config, 'mysql.example.internal', images)))
        subprocess.run(['helm', 'lint', str(chart), '-f', str(values)], check=True)
        rendered = subprocess.check_output(['helm', 'template', 'michelangelo', str(chart),
                                           '-n', 'ma-system', '-f', str(values)], text=True)
    env = {**os.environ, 'MYSQL_ROUTER_IMAGE': install.ROUTER_IMAGE,
           'MYSQL_ROUTER_HOST': 'mysql.example.internal',
           'MYSQL_ROUTER_CA_SECRET': 'database-ca', 'MYSQL_ROUTER_AUTH_SECRET': 'router-auth'}
    protected = subprocess.check_output([sys.executable, '-B', str(ROOT / 'installer/transport/render.py')],
                                        input=rendered, text=True, env=env)
    documents = [doc for doc in mysql_router.load(protected) if doc]
    checked = set()
    for doc in documents:
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
