#!/usr/bin/env python3
"""Application RPC credentials for the installer and dedicated workloads.

These credentials establish service identities. They never establish a human
identity or grant Kubernetes resource access beyond existing role bindings.
"""
import os
from pathlib import Path


def prepare_source(source):
    """Read a projected, rotating token for every SDK request; retain overrides."""
    path = Path(source) / 'python/michelangelo/api/v2/services/base.py'
    original = path.read_text()
    # Do not persist an injected token in the caller's reused request dictionary.
    # Explicit caller credentials still take precedence over the rotating file.
    old_headers = '        headers = request_headers or {}'
    new_headers = '        headers = dict(request_headers or {})'
    if old_headers in original:
        if original.count(old_headers) != 1:
            raise RuntimeError('SDK header provider copy layout differs from reviewed source')
        original = original.replace(old_headers, new_headers)
        path.write_text(original)
        path.chmod(0o644)
    elif new_headers not in original:
        raise RuntimeError('SDK header provider copy layout differs from reviewed source')
    marker = '# OCI application service identity: read the rotating token per RPC.'
    if marker in original:
        return
    anchor = '        return headers\n\n\nclass Context:'
    if original.count(anchor) != 1:
        raise RuntimeError('SDK header provider layout differs from the reviewed source')
    replacement = '''        # OCI application service identity: read the rotating token per RPC.
        if "authorization" not in headers:
            token_path = os.environ.get("MA_API_TOKEN_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/token")
            try:
                with open(token_path, encoding="utf-8") as stream:
                    token = stream.read().strip()
            except FileNotFoundError:
                token = ""
            if token:
                headers["authorization"] = "Bearer " + token
        return headers


class Context:'''
    path.write_text(original.replace(anchor, replacement))
    path.chmod(0o644)


def install(core):
    core.apply([
        {'apiVersion': 'v1', 'kind': 'ServiceAccount',
         'metadata': {'name': 'ma-installer', 'namespace': core.NS},
         'automountServiceAccountToken': False},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
         'metadata': {'name': 'michelangelo-api-token-verification'},
         'rules': [{'apiGroups': ['authentication.k8s.io'], 'resources': ['tokenreviews'], 'verbs': ['create']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
         'metadata': {'name': 'michelangelo-api-token-verification'},
         'subjects': [{'kind': 'ServiceAccount', 'name': 'michelangelo', 'namespace': core.NS}],
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': 'michelangelo-api-token-verification'}},
    ])
    token = core.run(['kubectl', '-n', core.NS, 'create', 'token', 'ma-installer', '--duration=4h'], capture=True).strip()
    if not token:
        raise RuntimeError('Installer application identity was not issued')
    path = core.save(core.WORK / 'installer-api-token', token)
    os.environ['MA_API_TOKEN_FILE'] = str(path)


def prepare_demo(source):
    """The reviewed lifecycle helper also uses a direct generated gRPC stub."""
    path = Path(source) / 'demo-source/oci_lifecycle/serving.py'
    text = path.read_text()
    if 'def service_metadata():' in text:
        return
    anchor = '\n\nclass NativeService:'
    if text.count(anchor) != 1:
        raise RuntimeError('Lifecycle native client layout differs from reviewed source')
    helper = '''

def service_metadata():
    token_path = os.environ.get('MA_API_TOKEN_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/token')
    with open(token_path, encoding='utf-8') as stream:
        token = stream.read().strip()
    if not token:
        raise RuntimeError('A dedicated workload identity is required')
    return (('rpc-caller', 'oci-lifecycle-service'),
            ('rpc-service', os.environ.get('MA_API_SERVER_NAME', 'ma-apiserver')),
            ('rpc-encoding', 'proto'), ('authorization', 'Bearer ' + token))


class NativeService:'''
    text = text.replace(anchor, helper)
    old = "metadata=(('rpc-caller','oci-lifecycle-pilot'),('rpc-service',os.environ.get('MA_API_SERVER_NAME','ma-apiserver')),('rpc-encoding','proto'))"
    if text.count(old) != 1:
        raise RuntimeError('Lifecycle native RPC metadata differs from reviewed source')
    path.write_text(text.replace(old, 'metadata=service_metadata()'))
    path.chmod(0o644)


def headers():
    path = os.environ.get('MA_API_TOKEN_FILE')
    if not path:
        raise RuntimeError('An authenticated installer identity is required')
    token = Path(path).read_text().strip()
    if not token:
        raise RuntimeError('Installer application identity is empty')
    return {'Authorization': 'Bearer ' + token}
