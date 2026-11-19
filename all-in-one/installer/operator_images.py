"""Render immutable, privately authenticated images for compatible operators."""
import base64
import json
import re


def image(config, name):
    reference = config[name + '_image']
    tagged, digest = reference.split('@')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Operator image requires an immutable digest')
    repository, tag = tagged.rsplit(':', 1)
    registry, repository = repository.split('/', 1)
    return {'registry': registry, 'repository': repository, 'tag': tag + '@' + digest}


def values(config):
    kyverno = {'global': {'imagePullSecrets': [{'name': 'michelangelo-registry'}]}}
    paths = {
        'admissionController.initContainer': 'kyverno_kyverno_init',
        'admissionController.container': 'kyverno_kyverno',
        'backgroundController': 'kyverno_background_controller',
        'cleanupController': 'kyverno_cleanup_controller',
        'reportsController': 'kyverno_reports_controller',
        'crds.migration': 'kyverno_kubectl_kyverno',
        'test': 'kyverno_readiness_checker',
        'webhooksCleanup': 'kyverno_readiness_checker',
    }
    for path, name in paths.items():
        node = kyverno
        for key in path.split('.'):
            node = node.setdefault(key, {})
        node['image'] = image(config, name)
    for component in ('admissionController', 'backgroundController', 'cleanupController', 'reportsController'):
        kyverno[component]['replicas'] = 1
    ray = image(config, 'kuberay')
    return {'image': {'repository': ray['registry'] + '/' + ray['repository'], 'tag': ray['tag']},
            'imagePullSecrets': [{'name': 'michelangelo-registry'}]}, kyverno


def prepare(core, config):
    ray, kyverno = values(config)  # Fail before mutation if any immutable image is missing.
    image(config, 'gateway')
    gateway = {'global': {'images': {'envoyGateway': {'image': config['gateway_image']}},
                          'imagePullSecrets': [{'name': 'michelangelo-registry'}]}}
    credentials = {'auths': {config['registry'].split('/')[0]: {
        'username': config['runtime_registry_username'], 'password': config['runtime_registry_password'],
        'auth': base64.b64encode((config['runtime_registry_username'] + ':' + config['runtime_registry_password']).encode()).decode()}}}
    for namespace in ('kuberay-system', 'kyverno', 'envoy-gateway-system'):
        core.apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace}})
        core.secret('michelangelo-registry', {'.dockerconfigjson': json.dumps(credentials)}, namespace, 'kubernetes.io/dockerconfigjson')
    for name, data in [('kuberay', ray), ('kyverno', kyverno), ('gateway', gateway)]:
        core.save(core.WORK / (name + '-values.json'), data)
