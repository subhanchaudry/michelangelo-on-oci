#!/usr/bin/env python3
"""Apply reviewed TLS transport and normalize official Temporal chart labels."""
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_router


def quote_secret_placeholders(text):
    # go.uber.org/config merges/serializes YAML before environment expansion.
    # A quoted plain ${KEY} loses its quotes during that merge. The colon-space
    # default forces YAML to retain quotes; defined Secrets still expand exactly.
    pattern = r"(?m)^(\s*(?:password|awsAccessKeyId|awsSecretAccessKey):[ \t]*)(?:['\"])?\$\{(MYSQL_PASSWORD|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)(?:: )?\}(?:['\"])?[ \t]*$"
    return re.sub(pattern, lambda match: match.group(1) + "'${" + match.group(2) + ": }'", text)


def publication_transcoding(text):
    config = mysql_router.yaml.safe_load(text)
    matches = []
    def visit(node):
        if isinstance(node, dict):
            if node.get('@type') == 'type.googleapis.com/envoy.extensions.filters.http.grpc_json_transcoder.v3.GrpcJsonTranscoder':
                matches.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(config)
    if len(matches) != 1 or not matches[0].get('services'):
        raise ValueError('Expected one explicit Envoy transcoder allowlist')
    # The publication reconciler reads sealed evaluations over this authenticated
    # API. Upstream derives its list from Studio imports, which omit this service.
    service = 'michelangelo.api.v2.EvaluationReportService'
    if service not in matches[0]['services']:
        matches[0]['services'].append(service)
    return mysql_router.yaml.safe_dump(config, sort_keys=False)


def render(text):
    # The upstream namespace setup Job includes two duplicate label names
    # via parent and Temporal helpers. Keep the Temporal schema labels.
    documents = re.split(r'(?m)^---\s*$', text)
    for index, document in enumerate(documents):
        if '# Source: michelangelo/templates/core/temporal-namespace-setup-job.yaml' not in document:
            continue
        for key, first, last in [('app.kubernetes.io/name', 'michelangelo', 'temporal'),
                                 ('app.kubernetes.io/component', 'temporal-namespace-setup', 'schema')]:
            matches = list(re.finditer(r'(?m)^    ' + re.escape(key) + r': ([^\r\n]+)\r?$', document))
            if len(matches) == 2:
                if [item.group(1).strip() for item in matches] != [first, last]:
                    raise ValueError('Unexpected namespace labels')
                item = matches[0]
                document = document[:item.start()] + document[item.end():]
        documents[index] = document
    output = mysql_router.transform('---'.join(documents), os.environ)
    docs = mysql_router.load(output)
    for document in docs:
        if document.get('kind') == 'ConfigMap':
            # Uber config expands environment references before merged YAML
            # decoding; special-leading generated Secrets must stay strings.
            for key, value in document.get('data', {}).items():
                if key.endswith('.yaml') and isinstance(value, str):
                    document['data'][key] = quote_secret_placeholders(value)
                    if key == 'envoy.yaml':
                        document['data'][key] = publication_transcoding(document['data'][key])
        spec = document.get('spec', {})
        pod = spec if document.get('kind') == 'Pod' else spec.get('template', {}).get('spec', {})
        if document.get('kind') == 'Deployment' and document.get('metadata', {}).get('name') == 'michelangelo-apiserver':
            for container in pod.get('containers', []):
                if container.get('name') == 'apiserver':
                    entries = [item for item in container.get('env', []) if item.get('name') != 'MA_IDENTITY_RESOLVE_URL']
                    entries.append({'name': 'MA_IDENTITY_RESOLVE_URL',
                                    'value': 'http://ma-identity.ma-system.svc.cluster.local:8081/internal/resolve'})
                    container['env'] = entries
        if document.get('kind') == 'Job' and document.get('metadata', {}).get('name') == 'michelangelo-temporal-namespace-setup':
            matches = [c for c in pod.get('containers', []) if c.get('name') == 'register-namespace']
            if len(matches) != 1:
                raise ValueError('Unexpected Temporal namespace setup container')
            container = matches[0]
            pull_secret = {'name': 'michelangelo-registry'}
            if pull_secret not in pod.setdefault('imagePullSecrets', []):
                pod['imagePullSecrets'].append(pull_secret)
            original = '\n'.join(container.get('command', []))
            match = re.search(r'tctl --address ([a-zA-Z0-9.-]+:[0-9]+) --namespace ([a-zA-Z0-9._-]+) namespace describe', original)
            if not match:
                raise ValueError('Unexpected Temporal namespace setup command')
            endpoint, namespace = match.groups()
            base = 'temporal --address ' + endpoint + ' --namespace ' + namespace + ' operator namespace '
            container['command'] = ['sh', '-ec',
                base + 'describe >/dev/null 2>&1 || ' + base +
                'create --retention 7d --description "Default Michelangelo namespace"']
        for container in pod.get('containers', []) + pod.get('initContainers', []):
            image = container['image']
            first = image.split('/')[0]
            if '/' not in image:
                container['image'] = 'docker.io/library/' + image
            elif '.' not in first and ':' not in first and first != 'localhost':
                container['image'] = 'docker.io/' + image
            if container['image'] in ('docker.io/library/busybox', 'docker.io/library/busybox:latest'):
                # The namespace wait helper is untagged upstream. Bind the
                # linux/amd64 binary exercised during OCI qualification.
                container['image'] = 'docker.io/library/busybox@sha256:f97baa533a26513c453a362be331a43eb60214302f449c16571b23cea141dce4'
            if container['image'] == 'docker.io/library/busybox:1.36':
                container['image'] += '@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662'
    return mysql_router.yaml.safe_dump_all(docs, explicit_start=True, sort_keys=False)


if __name__ == '__main__':
    try:
        sys.stdout.write(render(sys.stdin.read()))
    except Exception:
        print('Protected platform render failed; no partial manifest emitted', file=sys.stderr)
        raise SystemExit(1)
