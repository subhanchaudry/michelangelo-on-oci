#!/usr/bin/env python3
"""Build the reviewed Michelangelo release and accept a fresh OCI installation.

Run as root on the stack's Ubuntu bootstrap instance, using --config with a
0600 JSON file. Secrets travel through stdin or protected files, never argv.
The public edge is installed only after a real registered workflow succeeds.
"""
import argparse
import base64
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parent / '.runtime'
RELEASE = ROOT / 'release'
NS = 'ma-system'
APP = 'michelangelo'
UPSTREAM = 'ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3'
ROUTER_IMAGE = 'container-registry.oracle.com/mysql/community-router:26.10.0@sha256:3de30bd493403d2cc87f0a1070686911ded03e473161c4c235bddfe03dfc0d3b'
MYSQL_IMAGE = 'docker.io/library/mysql:8.0@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b'
ENVOY_IMAGE = 'docker.io/envoyproxy/envoy:v1.29-latest@sha256:5a292b91adc87aa56146fb9ee52fc85c30570d0f175d95b48cde8035a2e641dd'
TEMPORAL_IMAGE = 'docker.io/temporalio/server:1.24.2@sha256:16de74d893e813053b82551eb4463c795b368d2029520b4c8fe2e47e1c336bb9'
TEMPORAL_ADMIN = 'docker.io/temporalio/admin-tools:1.24.2-tctl-1.18.1-cli-0.13.0@sha256:a64874edd4fedecb9b6ca74b10cf9512f09522d676ce2af5bdaf06ab7adadd12'


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)
    return path


def log(message):
    print(datetime.datetime.now(datetime.timezone.utc).isoformat() + ' ' + message, flush=True)


def completed(report, phase):
    report['completed_phases'].append(phase)
    save(WORK / 'status.json', report)
    log('Completed ' + phase)


def run(args, *, cwd=None, env=None, stdin=None, capture=False, sensitive=False):
    """Never echo command arguments; sensitive command output goes to a private log."""
    if sensitive:
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, input=stdin,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        save(WORK / 'last-sensitive-command.log', result.stdout)
        if result.returncode:
            save(WORK / 'command-failures' / (str(time.time_ns()) + '.log'), result.stdout)
            raise RuntimeError('Protected operation failed; inspect last-sensitive-command.log on bootstrap instance')
        return result.stdout
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, input=stdin, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        if capture:
            save(WORK / 'last-command-error.log', (result.stdout or '') + (result.stderr or ''))
            save(WORK / 'command-failures' / (str(time.time_ns()) + '.log'), (result.stdout or '') + (result.stderr or ''))
        raise RuntimeError('Command failed: ' + Path(str(args[0])).name)
    return result.stdout if capture else ''


def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Michelangelo-OCI-Installer/1'})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def download(url, destination, checksum=None):
    data = get(url)
    if checksum and hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError('Download checksum failed: ' + Path(destination).name)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(data)
    return data


def extract(data, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive.getmembers():
            if item.name.startswith('/') or '..' in Path(item.name).parts:
                raise RuntimeError('Unsafe tool archive path')
        archive.extractall(destination, filter='data')


def python_environment():
    venv = WORK / 'venv'
    requirements = ['PyYAML==6.0.2', 'minio==7.2.18', 'temporalio==1.17.0',
                    'PyMySQL==1.1.2', 'oci-cli==3.94.1']
    marker = WORK / 'python-dependencies-ready.json'
    expected = {'requirements': requirements, 'pip_check': 'passed'}
    if not (venv / 'bin/python').exists():
        run(['python3', '-m', 'venv', venv])
    try:
        ready = json.loads(marker.read_text()) == expected
    except (OSError, ValueError):
        ready = False
    # Creating a venv is not proof that dependencies installed successfully.
    # Publish readiness only after resolution, consistency and lock recording.
    if not ready:
        run([venv / 'bin/python', '-m', 'pip', 'install', *requirements])
        run([venv / 'bin/python', '-m', 'pip', 'check'])
        save(WORK / 'python-dependencies.lock', run([venv / 'bin/python', '-m', 'pip', 'freeze'], capture=True))
        save(marker, expected)
    else:
        run([venv / 'bin/python', '-m', 'pip', 'check'])
    if Path(sys.executable).resolve() != (venv / 'bin/python').resolve():
        # sys.executable commonly resolves to the same system binary for both;
        # compare sys.prefix to preserve the venv's dependency search path.
        os.execv(str(venv / 'bin/python'), [str(venv / 'bin/python'), str(Path(__file__).resolve()), *sys.argv[1:]])
    if Path(sys.prefix) != venv:
        os.execv(str(venv / 'bin/python'), [str(venv / 'bin/python'), str(Path(__file__).resolve()), *sys.argv[1:]])
    os.environ['PATH'] = str(venv / 'bin') + ':' + os.environ['PATH']


def toolchains(kubernetes_version):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', kubernetes_version):
        raise RuntimeError('An exact Kubernetes version is required for kubectl')
    tools = WORK / 'tools'
    tools.mkdir(exist_ok=True)
    binpath = tools / 'bin'
    binpath.mkdir(exist_ok=True)
    os.environ['PATH'] = str(binpath) + ':' + str(tools / 'go/bin') + ':' + str(tools / 'node-v24.18.0-linux-x64/bin') + ':' + os.environ['PATH']
    if not (tools / 'go/bin/go').exists():
        releases = json.loads(get('https://go.dev/dl/?mode=json&include=all'))
        archive = next(f for r in releases if r['version'] == 'go1.26.8' for f in r['files']
                       if f['filename'] == 'go1.26.8.linux-amd64.tar.gz')
        extract(download('https://go.dev/dl/' + archive['filename'], tools / archive['filename'], archive['sha256']), tools)
    if not (tools / 'node-v24.18.0-linux-x64/bin/node').exists():
        name = 'node-v24.18.0-linux-x64.tar.xz'
        hashes = get('https://nodejs.org/dist/v24.18.0/SHASUMS256.txt').decode()
        checksum = next(line.split()[0] for line in hashes.splitlines() if line.split()[-1] == name)
        extract(download('https://nodejs.org/dist/v24.18.0/' + name, tools / name, checksum), tools)
        run(['npm', 'install', '--global', '--prefix', str(tools / 'node-v24.18.0-linux-x64'), 'yarn@1.22.22'])
    if not (binpath / 'helm').exists():
        url = 'https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz'
        checksum = get(url + '.sha256sum').decode().split()[0]
        extract(download(url, tools / 'helm.tar.gz', checksum), tools / 'helm-extract')
        shutil.copy2(tools / 'helm-extract/linux-amd64/helm', binpath / 'helm')
    kubectl = binpath / 'kubectl'
    client_version = ''
    if kubectl.exists():
        result = subprocess.run([str(kubectl), 'version', '--client', '-o', 'json'],
                                capture_output=True, text=True)
        if result.returncode == 0:
            client_version = json.loads(result.stdout)['clientVersion']['gitVersion']
    if client_version != kubernetes_version:
        url = 'https://dl.k8s.io/release/' + kubernetes_version + '/bin/linux/amd64/kubectl'
        staged = binpath / 'kubectl.download'
        download(url, staged, get(url + '.sha256').decode().strip())
        staged.chmod(0o755)
        staged.replace(kubectl)
    if not (binpath / 'buf').exists():
        env = dict(os.environ, GOBIN=str(binpath), GOMAXPROCS='4')
        run(['go', 'install', 'github.com/bufbuild/buf/cmd/buf@v1.57.2'], env=env)
    run(['docker', 'buildx', 'inspect', '--bootstrap'])


def kube(config):
    import yaml
    path = WORK / 'kubeconfig'
    if not path.exists():
        run(['oci', '--auth', 'instance_principal', '--region', config['region'], 'ce', 'cluster',
             'create-kubeconfig', '--cluster-id', config['cluster_id'], '--file', path,
             '--token-version', '2.0.0', '--kube-endpoint', 'PRIVATE_ENDPOINT'], sensitive=True)
        path.chmod(0o600)
    document = yaml.safe_load(path.read_text())
    for user in document['users']:
        arguments = user['user']['exec']['args']
        if '--auth' not in arguments:
            arguments.extend(['--auth', 'instance_principal'])
    save(path, yaml.safe_dump(document))
    os.environ['KUBECONFIG'] = str(path)
    run(['kubectl', 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=20m'])
    # IAM grants cluster-family operations, while Kubernetes role creation
    # separately requires an existing privileged RBAC binding.
    check = subprocess.run(['kubectl', 'auth', 'can-i', 'create', 'clusterroles'], capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != 'yes':
        group = config.get('bootstrap_dynamic_group_id')
        if not group:
            raise RuntimeError('Bootstrap dynamic group is required for the initial Kubernetes admin binding')
        apply({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
               'metadata': {'name': 'ma-bootstrap-installer'},
               'subjects': [{'kind': 'Group', 'name': group, 'apiGroup': 'rbac.authorization.k8s.io'}],
               'roleRef': {'kind': 'ClusterRole', 'name': 'cluster-admin', 'apiGroup': 'rbac.authorization.k8s.io'}})
        run(['kubectl', 'auth', 'can-i', 'create', 'clusterroles'])
    apply([{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS}},
           {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'default'}}])


def apply(resources):
    if isinstance(resources, dict):
        resources = [resources]
    run(['kubectl', 'apply', '-f', '-'], stdin=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': resources}), sensitive=True)


def secret(name, values, namespace=NS, kind='Opaque'):
    apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': namespace},
           'type': kind, 'stringData': values})


def pinned_image(image):
    if '@sha256:' in image:
        return image
    run(['docker', 'pull', '--platform', 'linux/amd64', image])
    identities = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', image], capture=True))
    if not identities:
        raise RuntimeError('Pulled image has no immutable identity')
    return identities[0]


def prepare_source():
    run([sys.executable, RELEASE / 'scripts/verify-review-bundle.py'])
    import source_release
    selected,_ = source_release.manifest()
    if selected['upstream_commit'] != UPSTREAM:
        raise RuntimeError('Installer and selected source provenance disagree')
    source = source_release.assemble(sys.modules[__name__])
    import identity_overlay
    import identity_ui
    import service_auth
    identity_overlay.prepare_source(source)
    identity_ui.prepare_source(source)
    service_auth.prepare_source(source)
    import qualification_overlay
    qualification_overlay.prepare_source(source)
    import workload_security
    workload_security.prepare_source(source)
    import dependency_overlay
    dependency_overlay.prepare_source(source)
    return source


def database(config):
    import pymysql
    host = config.get('mysql_expected_hostname', config['mysql_host'])
    ca = config.get('mysql_ca_pem')
    if not ca:
        raise RuntimeError('Verified MySQL server CA is required; configure the stack BYOC certificate')
    ca_path = save(WORK / 'mysql-ca.pem', ca)
    # BYOC hostname has an authoritative stack-configured IP. Bind locally for
    # bootstrap while preserving certificate hostname verification in PyMySQL.
    if host != config['mysql_host']:
        record = config['mysql_host'] + ' ' + host
        hosts = Path('/etc/hosts')
        if record not in hosts.read_text().splitlines():
            with hosts.open('a') as stream:
                stream.write('\n' + record + '\n')
    tls_context = ssl.create_default_context(cafile=str(ca_path))
    conn = pymysql.connect(host=host, user=config['mysql_username'], password=config['mysql_password'],
                           ssl=tls_context, connect_timeout=30)
    credentials_path = WORK / 'application-password.json'
    app_password = json.loads(credentials_path.read_text())['password'] if credentials_path.exists() else ''
    password_groups = ('abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '0123456789', '!@#%+_-')
    # HeatWave requires all four character classes; a failed initial bootstrap
    # may have persisted an alphanumeric password before CREATE USER rejected it.
    # Keep compliant existing credentials stable when installation is retried.
    if len(app_password) < 30 or not all(any(char in group for char in app_password) for group in password_groups):
        characters = [secrets.choice(group) for group in password_groups]
        characters.extend(secrets.choice(''.join(password_groups)) for _ in range(26))
        secrets.SystemRandom().shuffle(characters)
        app_password = ''.join(characters)
        save(credentials_path, {'password': app_password})
    with conn.cursor() as cursor:
        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        if not cursor.fetchone()[1]:
            raise RuntimeError('MySQL bootstrap TLS was not negotiated')
        for schema in ('michelangelo', 'temporal', 'temporal_visibility'):
            cursor.execute('CREATE DATABASE IF NOT EXISTS `' + schema + '`')
        cursor.execute("CREATE USER IF NOT EXISTS 'michelangelo_app'@'%%' IDENTIFIED BY %s REQUIRE SSL", (app_password,))
        # Router's local plaintext leg is confined to the pod, remote leg uses
        # TLS; REQUIRE SSL applies to the remote authenticated DB connection.
        cursor.execute("ALTER USER 'michelangelo_app'@'%%' IDENTIFIED BY %s REQUIRE SSL", (app_password,))
        for schema in ('michelangelo', 'temporal', 'temporal_visibility'):
            cursor.execute("GRANT ALL PRIVILEGES ON `" + schema + "`.* TO 'michelangelo_app'@'%'")
        identity_credentials = WORK / 'identity-database-password.json'
        if identity_credentials.exists():
            identity_password = json.loads(identity_credentials.read_text())['password']
        else:
            chars = [secrets.choice(group) for group in password_groups]
            chars.extend(secrets.choice(''.join(password_groups)) for _ in range(26))
            secrets.SystemRandom().shuffle(chars)
            identity_password = ''.join(chars)
            save(identity_credentials, {'password': identity_password})
        cursor.execute('CREATE DATABASE IF NOT EXISTS `michelangelo_identity`')
        cursor.execute("CREATE USER IF NOT EXISTS 'ma_identity'@'%%' IDENTIFIED BY %s REQUIRE SSL", (identity_password,))
        cursor.execute("ALTER USER 'ma_identity'@'%%' IDENTIFIED BY %s REQUIRE SSL", (identity_password,))
        cursor.execute("GRANT CREATE, ALTER, INDEX, SELECT, INSERT, UPDATE ON `michelangelo_identity`.* TO 'ma_identity'@'%'")
        config.update(identity_mysql_database='michelangelo_identity', identity_mysql_username='ma_identity',
                      identity_mysql_password=identity_password,
                      bootstrap_admin_emails=config.get('bootstrap_admin_emails') or config['authorized_emails'][:1],
                      initial_operator_emails=config['authorized_emails'])
    conn.commit()
    conn.close()
    secret('michelangelo-mysql', {'password': app_password})
    secret('michelangelo-mysql-ca', {'ca.pem': ca})
    secret('michelangelo-artifacts', {'AWS_ACCESS_KEY_ID': config['artifact_access_key'], 'AWS_SECRET_ACCESS_KEY': config['artifact_secret_key']})
    registry_credentials = {'auths': {config['registry'].split('/')[0]: {
        'username': config['runtime_registry_username'], 'password': config['runtime_registry_password'],
        'auth': base64.b64encode((config['runtime_registry_username'] + ':' + config['runtime_registry_password']).encode()).decode()}}}
    for namespace in (NS, 'default'):
        secret('michelangelo-registry', {'.dockerconfigjson': json.dumps(registry_credentials)}, namespace, 'kubernetes.io/dockerconfigjson')
    key, certificate = WORK / 'router-key.pem', WORK / 'router-cert.pem'
    if not key.exists():
        run(['openssl', 'req', '-x509', '-newkey', 'rsa:4096', '-sha256', '-nodes', '-days', '30',
             '-subj', '/CN=michelangelo-loopback-router', '-keyout', key, '-out', certificate], sensitive=True)
        key.chmod(0o600)
    secret('michelangelo-router-auth', {'tls.crt': certificate.read_text(), 'tls.key': key.read_text()}, kind='kubernetes.io/tls')
    return host


def values(config, host, images=None):
    images = images or {}
    endpoint = config['object_storage_namespace'] + '.compat.objectstorage.' + config['region'] + '.oraclecloud.com'
    stores = {}
    for store, db in (('default', 'temporal'), ('visibility', 'temporal_visibility')):
        stores[store] = {'driver': 'sql', 'sql': {'driver': 'mysql8', 'host': host, 'port': 3306,
            'database': db, 'user': 'michelangelo_app', 'password': '', 'existingSecret': 'michelangelo-mysql',
            'maxConns': 5, 'tls': {'enabled': True, 'caFile': '/mysql-ca/ca.pem', 'serverName': host, 'enableHostVerification': True}}}
    return {'fullnameOverride': APP, 'metadataStorage': {'driver': 'mysql', 'host': '127.0.0.1', 'port': 6446,
            'database': 'michelangelo', 'user': 'michelangelo_app', 'existingSecret': 'michelangelo-mysql', 'initImage': MYSQL_IMAGE},
        'objectStorage': {'endpoint': endpoint, 'secure': True, 'region': config['region'], 'bucket': config['artifact_bucket'], 'existingSecret': 'michelangelo-artifacts'},
        'workflow': {'engine': 'temporal', 'endpoint': APP + '-temporal-frontend:7233', 'domain': 'default'},
        'cadence': {'enabled': False}, 'imagePullSecrets': [{'name': 'michelangelo-registry'}],
        'images': {**{name: {'repository': images.get(name, {}).get('pinned', config['registry'] + ':' + name + '-0.11.0-operator.3'), 'tag': ''}
                      for name in ('apiserver', 'controllermgr', 'worker', 'ui')}, 'envoy': ENVOY_IMAGE,
                   'kuberayCollector': images.get('collector', {}).get('pinned', 'ghcr.io/michelangelo-ai/kuberay-collector:v1.5.0')},
        'podSecurityContext': {'runAsNonRoot': True, 'runAsUser': 65534, 'fsGroup': 65534, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'apiserver': {'service': {'type': 'ClusterIP'}, 'ingress': {'enabled': False}, 'metadataStorage': {'enable': True}, 'crdSync': {'enableUpdate': True}},
        'controllermgr': {'metadataStorage': {'enable': True}, 'minioUseEnvAws': True,
            'extraEnvFrom': [{'configMapRef': {'name': 'michelangelo-runtime-config'}}],
            'jobs': {'k8sengine': {'mapper': {'logPersistence': {'enabled': False, 's3DisableSSL': False}}}}},
        'ui': {'lifecycleLogProxy': 'ma-lifecycle-logs:8080', 'apiBaseUrl': '/api', 'apiProxy': APP + '-envoy:8081', 'basicAuthSecret': '', 'service': {'type': 'ClusterIP'}},
        'envoy': {'corsOrigins': '^https://' + re.escape(config['public_hostname']) + '$', 'service': {'type': 'ClusterIP'}},
        'inferenceServer': {'triton': {'defaultImage': images.get('triton', {}).get('pinned', '')},
            'gateway': {'name': 'ma-gateway', 'serviceName': 'ma-gateway', 'serviceNamespace': 'envoy-gateway-system', 'portName': 'http-8080'}},
        'temporal': {'enabled': True, 'fullnameOverride': APP + '-temporal',
            'server': {'image': {'repository': TEMPORAL_IMAGE.split(':1.24')[0], 'tag': '1.24.2@' + TEMPORAL_IMAGE.split('@')[1]},
                'replicaCount': 1, 'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'memory': '512Mi'}},
                'config': {'numHistoryShards': 16, 'persistence': stores},
                'additionalVolumes': [{'name': 'mysql-ca', 'secret': {'secretName': 'michelangelo-mysql-ca'}}],
                'additionalVolumeMounts': [{'name': 'mysql-ca', 'mountPath': '/mysql-ca', 'readOnly': True}]},
            'schema': {'createDatabase': {'enabled': False}, 'setup': {'enabled': True}, 'update': {'enabled': True}},
            'admintools': {'enabled': False, 'image': {'repository': 'docker.io/temporalio/admin-tools', 'tag': TEMPORAL_ADMIN.split(':', 1)[1]}},
            'web': {'enabled': False}, 'cassandra': {'enabled': False}, 'mysql': {'enabled': False}, 'postgresql': {'enabled': False},
            'elasticsearch': {'enabled': False}, 'prometheus': {'enabled': False}, 'grafana': {'enabled': False}},
        'monitoring': {'enabled': False}}


def build(config, source, initial_values):
    import image_permissions
    image_permissions.prepare(source)
    tag = json.loads((ROOT/'distribution.json').read_text())['release']
    package_manifest = ROOT.parent/'SOURCE-MANIFEST.json'
    if not package_manifest.is_file():
        raise RuntimeError('Build requires a packaged source manifest')
    manifest_sha256 = hashlib.sha256(package_manifest.read_bytes()).hexdigest()
    registry_host = config['registry'].split('/')[0]
    run(['docker', 'login', registry_host, '--username', config['registry_username'], '--password-stdin'],
        stdin=config['registry_password'] + '\n', sensitive=True)
    run(['helm', 'repo', 'add', 'cadence', 'https://cadence-workflow.github.io/cadence-charts'])
    run(['helm', 'repo', 'add', 'temporal', 'https://go.temporal.io/helm-charts'])
    run(['helm', 'repo', 'update'])
    path = save(WORK / 'build-values.json', initial_values)
    env = dict(os.environ, GOMAXPROCS='4', NODE_OPTIONS='--max-old-space-size=12288')
    run([sys.executable, RELEASE / 'scripts/build-upstream-release.py', '--source', source,
         '--repository', config['registry'], '--tag', tag, '--shared-repository', '--values', path, '--push'], env=env)
    images = json.loads((source / 'release-build/images.json').read_text())
    for name, details in images.items():
        if not details.get('digest'):
            raise RuntimeError('Built image lacks immutable digest: ' + name)
        details['pinned'] = details['image'].split(':')[0] + '@' + details['digest']
    if not (source / 'demo-source').exists():
        run([sys.executable, RELEASE / 'scripts/prepare-demo-image-context.py', '--source', source])
    import service_auth
    service_auth.prepare_demo(source)
    import qualification_overlay
    qualification_overlay.prepare_demo(source)
    import dependency_overlay
    dependency_overlay.prepare_demo(source)
    # Archive mode 0600 is appropriate for private configuration, but public
    # runtime source must remain readable by nonroot image users. Normalize
    # only the assembled copy; preserve the checksum-verified review payload.
    for asset in (source / 'demo-source').rglob('*'):
        if asset.is_symlink():
            raise RuntimeError('Assembled demo source contains a symlink')
        asset.chmod(0o755 if asset.is_dir() else 0o644)
    (source / 'demo-source').chmod(0o755)
    # Package the portable model delivery and inference source, using the
    # reviewed hash-locked dependencies. Models remain inactive until submitted.
    for target in ('delivery', 'triton'):
        image = config['registry'] + ':' + target + '-' + tag
        metadata = source / ('release-build/' + target + '-image.json')
        run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--push', '--target', target,
             '-f', 'docker/support-runtime.Dockerfile', '--metadata-file', metadata, '-t', image, '.'], cwd=source)
        digest = json.loads(metadata.read_text())['containerimage.digest']
        images[target] = {'image': image, 'digest': digest, 'pinned': config['registry'] + '@' + digest}
    # The collector source reference is the one selected by the tagged
    # Michelangelo build helper, independently reconstructed and recorded.
    collector_source = WORK / 'kuberay-source'
    if not collector_source.exists():
        run(['git', 'clone', '--branch', 'v1.6.1', '--single-branch', '--depth', '1',
             'https://github.com/ray-project/kuberay.git', collector_source])
    collector_commit = run(['git', 'rev-parse', 'HEAD'], cwd=collector_source, capture=True).strip()
    run(['git', 'diff', '--exit-code', 'HEAD', '--', 'historyserver'], cwd=collector_source)
    collector_build = dependency_overlay.prepare_collector(
        collector_source, source/'collector-build', collector_commit)
    image = config['registry'] + ':collector-' + tag
    metadata = source / 'release-build/collector-image.json'
    run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--push', '-f', 'Dockerfile.collector',
         '--metadata-file', metadata, '-t', image, '.'], cwd=collector_build)
    digest = json.loads(metadata.read_text())['containerimage.digest']
    images['collector'] = {'image': image, 'digest': digest, 'pinned': config['registry'] + '@' + digest,
                           'source_repository': 'https://github.com/ray-project/kuberay.git', 'source_commit': collector_commit}
    shutil.copy2(ROOT / 'cpu-runtime.Dockerfile', source / 'docker/oci-cpu-runtime.Dockerfile')
    image = config['registry'] + ':examples-cpu-' + tag
    metadata = source / 'release-build/examples-image.json'
    run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--push', '-f', 'docker/oci-cpu-runtime.Dockerfile',
         '--metadata-file', metadata, '-t', image, '.'], cwd=source)
    digest = json.loads(metadata.read_text())['containerimage.digest']
    images['examples'] = {'image': image, 'digest': digest, 'pinned': config['registry'] + '@' + digest,
                          'source_commit': UPSTREAM, 'python': '3.12', 'ray': '2.56.1', 'security_gate': 'requires-runtime-scan'}
    qualify_ray_runtime(images['examples']['pinned'])
    image = config['registry'] + ':identity-' + tag
    metadata = source / 'release-build/identity-image.json'
    run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--push',
         '--metadata-file', metadata, '-t', image, str(ROOT / 'identity-service')])
    digest = json.loads(metadata.read_text())['containerimage.digest']
    images['identity'] = {'image': image, 'digest': digest, 'pinned': config['registry'] + '@' + digest}
    import image_provenance
    images = image_provenance.pin(images, run)
    for item in images.values():
        item.update(installer_source_manifest_sha256=manifest_sha256, release=tag, platform='linux/amd64')
    config['identity_image'] = images['identity']['pinned']
    save(WORK / 'images.json', images)
    import support_images
    support_images.build(sys.modules[__name__], config, tag, manifest_sha256)
    return images


def qualify_ray_runtime(image):
    """Bound a credential-free workload-identity probe and always remove it."""
    name = 'ma-ray-qualification-' + secrets.token_hex(8)
    script = ROOT/'ray-runtime-smoke.py'
    script.chmod(0o644)
    report = {'image': image, 'uid': 65534, 'container': name, 'status': 'failed'}
    try:
        try:
            result = subprocess.run([
                'docker', 'run', '--name', name, '--user', '65534:65534',
                '--memory', '4g', '--cpus', '2', '--shm-size', '256m',
                '--pids-limit', '2048', '--env', 'HOME=/tmp', '--mount',
                'type=bind,src='+str(script)+',dst=/qualification.py,readonly',
                image, 'python3', '/qualification.py'],
                capture_output=True, text=True, timeout=180)
            save(WORK/'ray-qualification.log', result.stdout + result.stderr)
            report['exit_code'] = result.returncode
            if result.returncode:
                raise RuntimeError('Ray startup qualification failed; inspect ray-qualification.log')
            marker = {'ray_startup': 'passed', 'remote_task': 'passed', 'uid': 65534}
            if not any(line.strip() == json.dumps(marker) for line in result.stdout.splitlines()):
                raise RuntimeError('Ray qualification returned no verified remote-task result')
            report['status'] = 'passed'
        except subprocess.TimeoutExpired as error:
            def text(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
            save(WORK/'ray-qualification.log', text(error.stdout)+text(error.stderr))
            report['status'] = 'timed-out'
            raise RuntimeError('Ray startup qualification exceeded 180 seconds') from error
    finally:
        try:
            inspected = subprocess.run(['docker', 'inspect', name], capture_output=True,
                                       text=True, timeout=15)
            if inspected.returncode == 0:
                item = json.loads(inspected.stdout)[0]
                save(WORK/'ray-qualification-state.json',
                     {'state': item['State'], 'image': item['Image']})
        finally:
            try:
                cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True,
                                         text=True, timeout=30)
                report['container_removed'] = cleanup.returncode == 0 or 'No such container' in cleanup.stderr
            except subprocess.TimeoutExpired:
                report['container_removed'] = False
            if not report['container_removed']:
                report['status'] = 'cleanup-failed'
            save(WORK/'ray-qualification.json', report)
        if not report['container_removed']:
            raise RuntimeError('Ray qualification container cleanup failed')


def prerequisites(config, images):
    run(['helm', 'repo', 'add', 'kuberay', 'https://ray-project.github.io/kuberay-helm/'])
    run(['helm', 'repo', 'add', 'kyverno', 'https://kyverno.github.io/kyverno/'])
    run(['helm', 'repo', 'update'])
    run(['helm', 'upgrade', '--install', 'kuberay', 'kuberay/kuberay-operator', '--version', '1.5.0',
         '-n', 'kuberay-system', '--create-namespace', '--wait', '--timeout', '10m'])
    run(['helm', 'upgrade', '--install', 'envoy-gateway', 'oci://docker.io/envoyproxy/gateway-helm',
         '--version', 'v1.9.2', '-n', 'envoy-gateway-system', '--create-namespace', '--wait', '--timeout', '10m'])
    run(['helm', 'upgrade', '--install', 'kyverno', 'kyverno/kyverno', '--version', '3.5.2',
         '-n', 'kyverno', '--create-namespace', '--wait', '--timeout', '10m',
         '--set', 'admissionController.replicas=1', '--set', 'backgroundController.replicas=1',
         '--set', 'cleanupController.replicas=1', '--set', 'reportsController.replicas=1'])
    # Only create empty pilot leases on first deployment: never overwrite a
    # holder identity when an installer run is retried or upgraded.
    for name in ('michelangelo-pilot-serving', 'michelangelo-pilot-workflows'):
        probe = subprocess.run(['kubectl', '-n', NS, 'get', 'lease', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode:
            apply({'apiVersion': 'coordination.k8s.io/v1', 'kind': 'Lease', 'metadata': {'name': name, 'namespace': NS}, 'spec': {}})
    apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'michelangelo-runtime-config', 'namespace': NS},
           'data': {'MA_MODEL_DELIVERY_IMAGE': images['delivery']['pinned'], 'MA_MODEL_STORAGE_SECRET': 'michelangelo-runtime-artifacts',
                    'MA_MODEL_STORAGE_CONFIG': 'michelangelo-runtime-artifacts', 'MA_PILOT_NODE_SELECTOR': '{"workload.oci.oracle.com/pool":"cpu"}',
                    'MA_PILOT_NODE_TOLERATIONS': '[]', 'MA_GATEWAY_NAMESPACE': 'ma-gateway',
                    'MA_GATEWAY_CLUSTER_IP': 'true', 'MA_PILOT_WORKFLOW_QUEUE': 'true'}})
    apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'ma-gateway'}})
    apply({'apiVersion': 'gateway.networking.k8s.io/v1', 'kind': 'GatewayClass', 'metadata': {'name': 'ma-gateway'},
           'spec': {'controllerName': 'gateway.envoyproxy.io/gatewayclass-controller'}})
    apply({'apiVersion': 'gateway.envoyproxy.io/v1alpha1', 'kind': 'EnvoyProxy', 'metadata': {'name': 'ma-private-proxy', 'namespace': 'ma-gateway'},
           'spec': {'provider': {'type': 'Kubernetes', 'kubernetes': {'envoyService': {'type': 'ClusterIP', 'name': 'ma-gateway'}}}}})
    apply({'apiVersion': 'gateway.networking.k8s.io/v1', 'kind': 'Gateway', 'metadata': {'name': 'ma-gateway', 'namespace': 'ma-gateway'},
           'spec': {'gatewayClassName': 'ma-gateway', 'infrastructure': {'parametersRef': {'group': 'gateway.envoyproxy.io', 'kind': 'EnvoyProxy', 'name': 'ma-private-proxy'}},
                    'listeners': [{'name': 'http', 'protocol': 'HTTP', 'port': 8080, 'allowedRoutes': {'namespaces': {'from': 'All'}}}]}})


def deploy(config, source, host, images):
    import yaml
    import runtime
    # nginx resolves the protected log upstream when it starts; create its
    # private Service before Helm waits for UI readiness to avoid a cycle.
    runtime.prepare_log_service(sys.modules[__name__])
    values_file = save(WORK / 'values.json', values(config, host, images))
    env = dict(os.environ, MYSQL_ROUTER_IMAGE=config['mysql_router_image'], MYSQL_ROUTER_HOST=host, MYSQL_ROUTER_PORT='3306',
               MYSQL_ROUTER_CA_SECRET='michelangelo-mysql-ca', MYSQL_ROUTER_CA_KEY='ca.pem', MYSQL_ROUTER_AUTH_SECRET='michelangelo-router-auth',
               MYSQL_ROUTER_IMAGE_RECORD=str(WORK/'support-images.json'), MYSQL_ROUTER_SOURCE_MANIFEST=str(ROOT.parent/'SOURCE-MANIFEST.json'))
    # Preserve checksum-verified source chart; transport is a post-render step.
    args = ['helm', 'upgrade', '--install', APP, str(source / 'helm/michelangelo'), '-n', NS,
            '-f', str(values_file), '--post-renderer', sys.executable, '--post-renderer-args', str(ROOT / 'transport/render.py'),
            '--wait', '--timeout', '25m']
    run(['kubectl', '-n', NS, 'delete', 'job', APP + '-temporal-schema', APP + '-temporal-namespace-setup', '--ignore-not-found'], sensitive=True)
    run(args, env=env, sensitive=True)
    for component in ('apiserver', 'controllermgr', 'worker', 'ui', 'envoy', 'temporal-frontend', 'temporal-history', 'temporal-matching', 'temporal-worker'):
        run(['kubectl', '-n', NS, 'rollout', 'status', 'deployment/' + APP + '-' + component, '--timeout=10m'])
    run(['kubectl', '-n', NS, 'wait', '--for=condition=complete', 'job/' + APP + '-temporal-namespace-setup', '--timeout=10m'])


def functional(config, images):
    compatibility = {'deployment': {'enable_mutations': True, 'static_object_storage_credentials_approved': True},
        'platform': {'objectStorage': values(config, config.get('mysql_expected_hostname', config['mysql_host']))['objectStorage']}}
    config_path = save(WORK / 'functional-config.json', compatibility)
    credentials = save(WORK / 'artifact-key.json', {'id': config['artifact_access_key'], 'key': config['artifact_secret_key']})
    save(ROOT / 'upstream.lock.json', {'revision': UPSTREAM, 'images': {'worker': images['worker']['pinned']}})
    run([sys.executable, ROOT / 'tools/test_pipeline.py', '--execute', '--config', config_path,
         '--credentials', credentials, '--kubeconfig', WORK / 'kubeconfig', '--namespace', NS, '--timeout', '600'], sensitive=True)
    artifacts = sorted((ROOT / '.local/pipeline-validation').glob('*/evidence.json'), key=lambda x: x.stat().st_mtime)
    evidence = json.loads(artifacts[-1].read_text())
    if evidence['outcome'] != 'succeeded' or not all(evidence['checks'].values()):
        raise RuntimeError('Registered pipeline functional acceptance failed')
    save(WORK / 'functional-evidence.json', {'run_name': evidence['run_name'], 'outcome': evidence['outcome'], 'checks': evidence['checks'], 'limitations': evidence['limitations']})


def public_edge(config):
    import auth
    config = dict(config)
    config['caddy_image'] = pinned_image(config.get('caddy_image', auth.DEFAULT_CADDY))
    config['oauth2_proxy_image'] = pinned_image(config.get('oauth2_proxy_image', auth.DEFAULT_PROXY))
    auth.install(config, namespace=NS, ui_service='ma-identity', ui_port=8080)
    run(['kubectl', '-n', NS, 'rollout', 'status', 'deployment/ma-browser-edge', '--timeout=15m'])
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        try:
            result = auth.verify_public(config)
            save(WORK / 'public-edge-evidence.json', result)
            return result
        except (OSError, RuntimeError, urllib.error.URLError):
            time.sleep(15)
    raise RuntimeError('Public HTTPS/login enforcement acceptance timed out; inspect bootstrap status and browser-edge logs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    WORK.mkdir(exist_ok=True)
    import fcntl
    lock = (WORK / 'installer.lock').open('w')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('Waiting for the active installer to finish before retrying')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    if args.config.stat().st_mode & 0o077:
        raise RuntimeError('Installer config must be mode 0600')
    config = json.loads(args.config.read_text())
    required = ('cluster_id', 'kubernetes_version', 'region', 'mysql_host', 'mysql_username', 'mysql_password', 'registry', 'registry_username',
                'registry_password', 'object_storage_namespace', 'artifact_bucket', 'artifact_access_key', 'artifact_secret_key',
                'runtime_registry_username', 'runtime_registry_password', 'runtime_artifact_access_key', 'runtime_artifact_secret_key',
                'public_hostname', 'oidc_issuer', 'oidc_client_id', 'oidc_client_secret', 'authorized_emails', 'acme_email')
    if any(not config.get(key) for key in required):
        raise RuntimeError('Installer config is incomplete')
    if config.get('namespace', NS) != NS:
        raise RuntimeError('Reviewed operator release requires ma-system namespace')
    python_environment()
    distribution = json.loads((ROOT / 'distribution.json').read_text())
    report = {'started': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'release': distribution['release'],
              'distribution_release': distribution['release'],
              'status': 'installing', 'completed_phases': [], 'application_url': 'https://' + config['public_hostname'],
              'production_ready': False, 'interactive_login_verified': False}
    save(WORK / 'status.json', report)
    upgrade_checkpoint = None
    import upgrade_control
    try:
        for name, action in [('toolchains', lambda: toolchains(config['kubernetes_version'])), ('cluster_access', lambda: kube(config))]:
            log('Starting ' + name)
            action()
            completed(report, name)
        source = prepare_source()
        upgrade_checkpoint = upgrade_control.begin(sys.modules[__name__])
        completed(report, 'upgrade_checkpoint_and_drain')
        host = database(config)
        completed(report, 'verified_database_bootstrap')
        images = build(config, source, values(config, host))
        completed(report, 'reviewed_source_images')
        prerequisites(config, images)
        completed(report, 'cluster_prerequisites')
        import service_auth
        service_auth.install(sys.modules[__name__])
        import identity
        identity.install(config, namespace=NS)
        completed(report, 'persistent_user_identity_and_access')
        deploy(config, source, host, images)
        completed(report, 'application_ready')
        import runtime
        runtime.install(config, images)
        import qualification_runtime
        qualification_runtime.install(sys.modules[__name__], images)
        completed(report, 'runtime_registrations_and_rotation')
        import seed
        seed.install(config, images)
        completed(report, 'studio_training_and_lifecycle_catalog')
        upgrade_control.allow_installer_probe(sys.modules[__name__])
        functional(config, images)
        completed(report, 'registered_pipeline_and_model')
        public_edge(config)
        completed(report, 'public_https_unauthenticated_denial')
        upgrade_control.set_gate(sys.modules[__name__], False)
        upgrade_control.complete(sys.modules[__name__])
        report['status'] = 'ready_for_iam_browser_login'
        report['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(WORK / 'status.json', report)
        log('Application functional and public HTTPS identity edge accepted; actual browser IAM login still needs verification')
    except Exception as error:
        if upgrade_checkpoint is not None:
            try:
                upgrade_control.rollback(sys.modules[__name__], upgrade_checkpoint)
                report['rollback'] = 'application configuration restored; maintenance retained'
            except Exception as recovery_error:
                report['rollback'] = 'maintenance retained; recovery requires inspection: ' + type(recovery_error).__name__
        report['status'] = 'failed'
        report['error_type'] = type(error).__name__
        if isinstance(error, RuntimeError):
            report['error'] = str(error)
        save(WORK / 'status.json', report)
        save(WORK / 'failures' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json'), report)
        # Avoid printing exception text from third-party clients, which can
        # include secrets. User-readable sanitized failure context is above.
        log('Installation failed (' + type(error).__name__ + '); review protected bootstrap diagnostics')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
