"""Build pinned workflow, proxy and database-client dependencies for this release."""
import json
from pathlib import Path
import shutil
import tempfile

SOURCES = {
    'gateway': ('https://github.com/envoyproxy/gateway.git', 'v1.9.2', 'f9f92a112a4033abed5c8d43a5f7e37da257403c'),
    'caddy': ('https://github.com/caddyserver/caddy.git', 'v2.11.9', '0d98caea58f794f88e709b1bef2880996a471bca'),
    'temporal': ('https://github.com/temporalio/temporal.git', 'v1.24.2', '5677de76317b572458b8f151525886df87869972'),
    'kuberay': ('https://github.com/ray-project/kuberay.git', 'v1.7.2', 'fb5edf56ce1e2783bf3adbc4fbec97488ef208f1'),
    'kyverno': ('https://github.com/kyverno/kyverno.git', 'v1.19.2', 'e63308f1e16c05cd009ac4864be6ec25c66b49be'),
    'dockerize': ('https://github.com/jwilder/dockerize.git', 'v0.16.0', '079eb740a8fecb12b7705bfaa1abb06888c60c2e'),
}


def source(core, name):
    repository, tag, commit = SOURCES[name]
    path = core.WORK / ('support-source-' + name + '-' + commit)
    if not path.exists():
        core.run(['git', 'clone', '--depth', '1', '--single-branch', '--branch', tag, repository, path])
    if core.run(['git', 'rev-parse', 'HEAD'], cwd=path, capture=True).strip() != commit:
        raise RuntimeError('Support source tag differs from its pinned commit')
    if core.run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=path, capture=True).strip():
        raise RuntimeError('Support source checkout has unreviewed changes')
    return path


def build(core, config, tag, manifest_sha256):
    import image_provenance
    context = Path(tempfile.mkdtemp(prefix='release-support-build-', dir=core.WORK))
    for name in SOURCES:
        shutil.copytree(source(core, name), context / name, ignore=shutil.ignore_patterns('.git'))
    for dependency, destination in [('gateway', 'gateway'), ('caddy', 'caddy'), ('temporal', 'temporal'), ('kuberay', 'kuberay/ray-operator'), ('kyverno', 'kyverno')]:
        for filename in ('go.mod', 'go.sum'):
            shutil.copyfile(core.ROOT / 'dependencies' / dependency / filename, context / destination / filename)
    images = json.loads((core.WORK / 'support-images.json').read_text())
    builds = [
        ('caddy', 'caddy-runtime.Dockerfile', None),
        ('mysql_client', 'mysql-client-runtime.Dockerfile', None),
        ('envoy', 'envoy-runtime.Dockerfile', None),
        ('temporal_admin', 'temporal-runtime.Dockerfile', 'admin'),
        ('temporal_server', 'temporal-runtime.Dockerfile', 'server'),
    ]
    operator_binaries = {'gateway': 'envoy-gateway', 'kuberay': 'kuberay', **{'kyverno_' + n.replace('-', '_'): n for n in ('kyverno', 'kyverno-init', 'kubectl-kyverno', 'background-controller', 'cleanup-controller', 'reports-controller', 'readiness-checker')}}
    builds += [(name, 'operator-runtime.Dockerfile', name if name in ('gateway', 'kuberay') else 'kyverno') for name in operator_binaries]
    for name, dockerfile, target in builds:
        shutil.copyfile(core.ROOT / dockerfile, context / dockerfile)
        image = config['registry'] + ':' + name.replace('_', '-') + '-' + tag
        metadata = core.WORK / (name + '-build.json')
        args = ['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--push',
                '--metadata-file', metadata, '-f', context / dockerfile, '-t', image]
        if name in operator_binaries:
            args += ['--build-arg', 'BINARY=' + operator_binaries[name]]
        if name == 'envoy':
            args += ['--no-cache-filter', 'runtime-security']
        if target:
            args += ['--target', target]
        args.append(context)
        core.run(args)
        digest = json.loads(metadata.read_text())['containerimage.digest']
        record = image_provenance.pin({name: {'image': image, 'digest': digest,
            'pinned': config['registry'] + '@' + digest}}, core.run)[name]
        if name == 'gateway':
            # The chart's pre-install/upgrade hook overrides the entrypoint.
            core.run(['docker', 'run', '--rm', '--platform', 'linux/amd64',
                      '--entrypoint', 'envoy-gateway', record['pinned'],
                      'certgen', '--help'], capture=True)
            record['chart_certgen_command_verified'] = True
        record.update(installer_source_manifest_sha256=manifest_sha256,
                      platform='linux/amd64', release=tag, security_gate='requires-runtime-scan')
        images[name] = record
        # Helm dependencies concatenate repository:tag; retain the tag alongside
        # the immutable executable digest rather than producing repository:@digest.
        config[name + '_image'] = image + '@' + record['digest']
        core.save(core.WORK / 'support-images.json', images)
    return images
