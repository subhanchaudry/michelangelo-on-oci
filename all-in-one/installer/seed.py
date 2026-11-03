#!/usr/bin/env python3
"""Seed visible fresh-site projects, CPU training, lifecycle pipelines and targets.

No schedule, training run or serving compute is activated here. Existing native
resource identities are preserved when the installer is retried.
"""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
if '--compile-training' not in sys.argv:
    import install as core


def create_once(kind, resource):
    namespace = resource['metadata']['namespace']
    name = resource['metadata']['name']
    exists = subprocess.run(['kubectl', '-n', namespace, 'get', kind + '.michelangelo.api', name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if exists.returncode:
        core.run(['kubectl', 'create', '-f', '-'], stdin=json.dumps(resource), sensitive=True)
    elif kind == 'pipelines':
        # Seeded pipelines must follow the currently built image when a
        # retry fixes a runtime dependency. Preserve native identity/status.
        current = json.loads(core.run(['kubectl', '-n', namespace, 'get', kind + '.michelangelo.api', name, '-o', 'json'], capture=True))
        owned = current.get('metadata', {}).get('annotations', {})
        expected = resource['metadata'].get('annotations', {})
        if owned.get('michelangelo.ai/demo') != expected.get('michelangelo.ai/demo') or not owned.get('michelangelo.ai/workflow-package-sha256'):
            raise RuntimeError('Existing pipeline is not an installer-owned catalog entry: ' + namespace + '/' + name)
        if current.get('spec') != resource['spec'] or any(owned.get(key) != value for key, value in expected.items()):
            core.run(['kubectl', '-n', namespace, 'patch', kind + '.michelangelo.api', name,
                      '--type=merge', '--patch-file=/dev/stdin'],
                     stdin=json.dumps({'spec': resource['spec'], 'metadata': {'annotations': expected}}), sensitive=True)

    elif kind == 'inferenceservers':
        current = json.loads(core.run(['kubectl','-n',namespace,'get',kind+'.michelangelo.api',name,'-o','json'],capture=True))
        old, desired = current.get('spec',{}), resource['spec']
        if (old.get('clusterTargets') != desired.get('clusterTargets') or old.get('backendType') != desired.get('backendType') or
            current.get('metadata',{}).get('labels',{}).get('michelangelo/environment') != 'nonprod'):
            raise RuntimeError('Refusing to adopt an unrelated serving target: '+namespace+'/'+name)
        if not old.get('suspended') or current.get('status',{}).get('state') != 'INFERENCE_SERVER_STATE_SUSPENDED':
            raise RuntimeError('Serving target must be suspended before image reconciliation')
        if old.get('initSpec') != desired['initSpec']:
            core.run(['kubectl','-n',namespace,'patch',kind+'.michelangelo.api',name,'--type=merge','--patch-file=/dev/stdin'],
                stdin=json.dumps({'metadata':{'annotations':{'michelangelo.ai/managed-by':'consolidated-installer'}},
                                 'spec':{'initSpec':desired['initSpec']}}),sensitive=True)


def compiler_main():
    """Runs inside the source-built CPU image, without deployment credentials."""
    from michelangelo.uniflow.core.build import build
    from michelangelo.gen.api.typed_struct_pb2 import TypedStruct
    import base64
    manifest = json.loads(Path('/out/compiler-input.json').read_text())
    package_dir = Path('/out/training')
    package_dir.mkdir(exist_ok=True)
    import oci_demos.workflows as module
    entries = []
    demos = [('ranking-smoke', 'oci-ranking-showcase'), ('ranking-mlops', 'oci-ranking-showcase'),
             ('ranking-advanced', 'oci-ranking-showcase'), ('movielens-cf', 'oci-movielens-showcase'),
             ('fraud-anomaly', 'oci-fraud-anomaly-showcase'), ('housing-synthetic', 'oci-california-housing-showcase'),
             ('california-housing-xgb', 'oci-california-housing-showcase'), ('bert-cola-finetuning', 'oci-bert-cola-showcase')]
    for demo, namespace in demos:
        module_name = 'oci_demos.training_workflows' if demo in ('california-housing-xgb','bert-cola-finetuning') else 'oci_demos.workflows'
        module = importlib.import_module(module_name)
        function = demo.replace('-', '_')
        package = build(getattr(module, function)).to_tarball_bytes()
        checksum = hashlib.sha256(package).hexdigest()
        filename = demo + '-' + checksum + '.tar.gz'
        (package_dir / filename).write_bytes(package)
        uri = manifest['storage_url'] + '/packages/' + demo + '/' + checksum + '.tar.gz'
        env = {'UF_STORAGE_URL': manifest['storage_url'] + '/' + namespace + '/workflows',
               'UF_TASK_IMAGE': manifest['task_image'], 'UF_PLUGIN_RAY_USE_FSSPEC': '1', 'IMAGE_PULL_POLICY': 'IfNotPresent',
               'REGISTRY_NAMESPACE': namespace, 'REGISTRY_ENDPOINT': manifest['registry_endpoint'],
               'MA_PILOT_CLUSTER_TARGET': json.dumps(manifest['cluster_target'], separators=(',', ':')),
               'MA_REGISTER_LIFECYCLE': 'true', 'HOME': '/tmp', 'HF_HOME': '/tmp/huggingface', 'XDG_CACHE_HOME': '/tmp/cache'}
        config = TypedStruct(type_url='type.googleapis.com/michelangelo/UniFlowConf')
        config.value.update({'environ': env})
        entries.append({'apiVersion': 'michelangelo.api/v2', 'kind': 'Pipeline', 'metadata': {'name': demo, 'namespace': namespace,
            'annotations': {'michelangelo/uniflow-image': manifest['task_image'], 'michelangelo.ai/demo': demo,
                            'michelangelo.ai/workflow-package-sha256': checksum}},
            'spec': {'description': 'Source-built CPU training with persisted model registration', 'type': 'PIPELINE_TYPE_TRAIN',
                'manifest': {'type': 'PIPELINE_MANIFEST_TYPE_UNIFLOW', 'filePath': module_name,
                    'uniflowFunction': module_name + '.' + function, 'uniflowTar': uri,
                    'content': {'@type': 'type.googleapis.com/michelangelo.api.TypedStruct', 'value': base64.b64encode(config.SerializeToString()).decode()}}}})
    (package_dir / 'pipelines.json').write_text(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': entries}))


def install(config, images):
    from minio import Minio
    import yaml
    release = core.RELEASE / 'platform/releases/v0.11.0'
    catalog = json.loads((core.RELEASE / 'platform/serving/lifecycle/catalog.json').read_text())
    kubeconfig = yaml.safe_load((core.WORK / 'kubeconfig').read_text())['clusters'][0]['cluster']
    from urllib.parse import urlsplit
    server = urlsplit(kubeconfig['server'])
    cluster = {'clusterId': 'oci-serving', 'kubernetes': {'host': server.scheme + '://' + server.hostname,
               'port': str(server.port or 443), 'tokenTag': 'cluster-oci-serving-client-token', 'caDataTag': 'cluster-oci-serving-ca-data'}}
    # Project definitions make the preconfigured namespaces visible in Studio.
    for namespace in sorted({model['project'] for model in catalog['models']}):
        create_once('projects', {'apiVersion': 'michelangelo.api/v2', 'kind': 'Project',
            'metadata': {'name': namespace, 'namespace': namespace, 'annotations': {'michelangelo/worker_queue': 'default'}},
            'spec': {'description': 'OCI CPU model lifecycle demonstration', 'tier': 5,
                'gitRepo': 'https://github.com/michelangelo-ai/michelangelo.git', 'rootDir': 'python/examples',
                'owner': {'owners': ['oci-platform-operator'], 'owningTeam': '00000000-0000-4000-8000-000000000000'}}})
    for model in catalog['models']:
        namespace, demo = model['project'], model['demo']
        target_name = demo + '-cpu'
        create_once('modelfamilys', {'apiVersion': 'michelangelo.api/v2', 'kind': 'ModelFamily',
            'metadata': {'name': demo, 'namespace': namespace}, 'spec': {'name': demo, 'description': 'OCI model family'}})
        create_once('inferenceservers', {'apiVersion': 'michelangelo.api/v2', 'kind': 'InferenceServer',
            'metadata': {'name': target_name, 'namespace': namespace, 'labels': {'michelangelo/environment': 'nonprod'}},
            'spec': {'tenancyType': 'TENANCY_TYPE_DEDICATED', 'backendType': 'BACKEND_TYPE_TRITON', 'suspended': True,
                     'initSpec': {'numInstances': 1, 'servingSpec': {'image': {'uri': images['triton']['pinned'],
                        'imagePullPolicy': 'IfNotPresent', 'imagePullSecrets': ['ma-lifecycle-runtime-registry']}}},
                     'clusterTargets': [cluster]}})
        create_once('deployments', {'apiVersion': 'michelangelo.api/v2', 'kind': 'Deployment',
            'metadata': {'name': demo + '-deployment', 'namespace': namespace},
            'spec': {'inferenceServer': {'name': target_name, 'namespace': namespace},
                     'modelFamily': {'name': demo, 'namespace': namespace}, 'definition': {'type': 'TARGET_TYPE_INFERENCE_SERVER', 'subType': 'realtime-serving'},
                     'strategy': {'rolling': {'incrementPercentage': 100}}}})
    output = core.WORK / 'compiled-workflows'
    output.mkdir(exist_ok=True)
    storage_url = 's3://' + config['artifact_bucket'] + '/studio-demos/lifecycle'
    core.save(output / 'cluster-target.json', cluster)
    core.save(output / 'compiler-input.json', {'task_image': images['examples']['pinned'], 'storage_url': storage_url,
              'registry_endpoint': 'michelangelo-apiserver.ma-system.svc.cluster.local:15566', 'cluster_target': cluster})
    # A separate output revision prevents overwriting an earlier compiled set.
    suffix = hashlib.sha256(images['examples']['pinned'].encode()).hexdigest()[:16]
    packages = output / ('lifecycle-' + suffix)
    mount_args = ['docker', 'run', '--rm', '--user', '0', '--mount', 'type=bind,src=' + str(core.RELEASE) + ',dst=/release,readonly',
                  '--mount', 'type=bind,src=' + str(output) + ',dst=/out',
                  '--mount', 'type=bind,src=' + str(Path(__file__).resolve()) + ',dst=/seed.py,readonly', images['examples']['pinned']]
    if not packages.exists():
        core.run(mount_args + ['python3', '/release/scripts/package-upstream-lifecycle.py',
            '--task-image', images['examples']['pinned'], '--storage-url', storage_url, '--cluster-target', '/out/cluster-target.json',
            '--registry-endpoint', 'michelangelo-apiserver.ma-system.svc.cluster.local:15566', '--output', '/out/' + packages.name])
    # seed.py's compiler imports no installer dependencies; its entrypoint sets
    # an explicit mode before any cloud-facing code can execute.
    compiler_revision = hashlib.sha256((output / 'compiler-input.json').read_bytes() + Path(__file__).read_bytes()).hexdigest()
    training_marker = output / 'training/compiler-revision.txt'
    if not (output / 'training/pipelines.json').exists() or not training_marker.exists() or training_marker.read_text() != compiler_revision:
        core.run(mount_args + ['python3', '/seed.py', '--compile-training'])
        training_marker.write_text(compiler_revision)
    endpoint = config['object_storage_namespace'] + '.compat.objectstorage.' + config['region'] + '.oraclecloud.com'
    storage = Minio(endpoint, access_key=config['artifact_access_key'], secret_key=config['artifact_secret_key'], secure=True, region=config['region'])
    count = 0
    catalog_entries = []
    for directory in (packages, output / 'training'):
        entries = json.loads((directory / 'pipelines.json').read_text())['items']
        for entry in entries:
            uri = entry['spec']['manifest']['uniflowTar']
            checksum = entry['metadata']['annotations']['michelangelo.ai/workflow-package-sha256']
            package = next(directory.glob(entry['metadata']['name'] + '-' + checksum + '.tar.gz'))
            payload = package.read_bytes()
            if hashlib.sha256(payload).hexdigest() != checksum:
                raise RuntimeError('Compiled workflow checksum differs from metadata')
            key = urlsplit(uri).path.lstrip('/')
            import io
            storage.put_object(config['artifact_bucket'], key, io.BytesIO(payload), len(payload), content_type='application/gzip')
            readback = storage.get_object(config['artifact_bucket'], key)
            try:
                if hashlib.sha256(readback.read()).hexdigest() != checksum:
                    raise RuntimeError('Published workflow package integrity failed')
            finally:
                readback.close()
                readback.release_conn()
            create_once('pipelines', entry)
            catalog_entries.append(entry)
            count += 1
    import pipeline_revisions
    pipeline_revisions.install(core, catalog_entries)
    core.save(core.WORK / 'catalog-evidence.json', {'projects': len({model['project'] for model in catalog['models']}),
              'suspended_targets': len(catalog['models']), 'pipelines_registered': count,
              'models_trained': 0, 'training_runs_started': 0, 'serving_compute_activated': False,
              'limitations': ['Eight source training flows registered; registration is not training acceptance.',
                             'Train every model and verify publication on these exact executable images. Capacity failures remain blockers.']})


if __name__ == '__main__' and '--compile-training' in sys.argv:
    compiler_main()
