"""Publish immutable installer snapshots for Studio's revision-based views.

These are installer resource snapshots, not fabricated Git commits. Historical
PipelineRun records and actor attribution are never rewritten.
"""
import copy
import hashlib
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

SOURCE = 'michelangelo.ai/ConsolidatedInstaller'
DIGEST = 'michelangelo.ai/pipeline-snapshot-sha256'


def build_revision(pipeline):
    meta = pipeline['metadata']
    annotations = meta.get('annotations', {})
    if not annotations.get('michelangelo.ai/workflow-package-sha256') or not annotations.get('michelangelo.ai/demo'):
        raise RuntimeError('Only installer-owned catalog pipelines may be snapshotted')
    if pipeline.get('status', {}).get('state') not in (3, 'PIPELINE_STATE_READY'):
        raise RuntimeError('Pipeline must be ready before publishing a revision')
    identity = {'snapshot_format': 2, 'spec': pipeline['spec'], 'annotations': {
        k: v for k, v in annotations.items() if k.startswith('michelangelo.ai/')}}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    name = 'pipeline-' + meta['name'] + '-' + digest[:12]
    content = copy.deepcopy(pipeline)
    # API reads attach the database row identity. It is provenance of the
    # existing object, not part of a runnable definition: copying it into a new
    # run would be rejected (and must never overwrite the historical row).
    content['metadata'].get('annotations', {}).pop('michelangelo/MetadataStoragePrimaryKey', None)
    content['status'].pop('latestRevision', None)
    return {'metadata': {'name': name, 'namespace': meta['namespace'],
                         'labels': {'michelangelo/PipelineType': str(pipeline['spec']['type'])},
                         'annotations': {DIGEST: digest, 'michelangelo.ai/source-pipeline-uid': meta['uid']}},
            'spec': {'baseType': {'apiVersion': 'michelangelo.api/v2', 'kind': 'Pipeline'},
                     'baseResource': {'namespace': meta['namespace'], 'name': meta['name']},
                     'revisionId': digest, 'source': SOURCE,
                     'content': {'@type': 'type.googleapis.com/michelangelo.api.v2.Pipeline', **content}}}


def ensure_revision(api, revision):
    """Read through both native and archived storage; never recreate history."""
    namespace, name = revision['metadata']['namespace'], revision['metadata']['name']
    try:
        stored = api('Revision', 'Get', {'namespace': namespace, 'name': name})['revision']
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
        try:
            api('Revision', 'Create', {'revision': revision})
        except urllib.error.HTTPError as conflict:
            if conflict.code != 409:
                raise
            conflict.close()
            # A concurrent creator/ingester may have made the same identity
            # visible. Its contents must still pass every check below.
        stored = api('Revision', 'Get', {'namespace': namespace, 'name': name})['revision']
    meta, expected = stored['metadata'], revision['metadata']
    annotations = meta.get('annotations', {})
    if (meta.get('name') != name or meta.get('namespace') != namespace or not meta.get('uid') or
        annotations.get(DIGEST) != expected['annotations'][DIGEST] or
        annotations.get('michelangelo.ai/source-pipeline-uid') != expected['annotations']['michelangelo.ai/source-pipeline-uid'] or
        any(stored['spec'].get(key) != revision['spec'][key] for key in ('source', 'revisionId', 'baseType', 'baseResource'))):
        raise RuntimeError('Existing revision conflicts with installer snapshot: ' + name)
    content = copy.deepcopy(stored['spec']['content'])
    content.pop('@type', None)
    if build_revision(content)['metadata']['annotations'][DIGEST] != expected['annotations'][DIGEST]:
        raise RuntimeError('Stored revision content differs from its digest: ' + name)
    return stored


def require_ready_archive(stored):
    if (stored.get('status', {}).get('state') not in (3, 'REVISION_STATE_READY') or
        stored['metadata'].get('annotations', {}).get('michelangelo/Immutable') != 'true'):
        raise RuntimeError('Archived installer revision is not immutable and ready')


def install(core, entries):
    import service_auth
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with (core.WORK/'revision-forward.log').open('w') as log:
        process = subprocess.Popen(['kubectl', '-n', core.NS, 'port-forward', '--address', '127.0.0.1',
                                    'service/'+core.APP+'-envoy', str(port)+':8081'], stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+30
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Revision API forward failed')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=1): break
                except OSError:
                    if time.monotonic() >= deadline: raise RuntimeError('Revision API forward timed out')
                    time.sleep(.25)

            def api(kind, action, body):
                headers = {**service_auth.headers(), 'Content-Type': 'application/json',
                           'rpc-service': 'ma-apiserver', 'rpc-encoding': 'proto',
                           'rpc-caller': 'consolidated-installer',
                           'context-ttl-ms': '30000', 'grpc-timeout': '30000m'}
                request = urllib.request.Request(
                    'http://127.0.0.1:'+str(port)+'/michelangelo.api.v2.'+kind+'Service/'+action+kind,
                    data=json.dumps(body).encode(), headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response: return json.load(response)

            evidence = []
            for entry in entries:
                meta = entry['metadata']; namespace, name = meta['namespace'], meta['name']
                deadline = time.monotonic()+60
                while True:
                    pipeline = api('Pipeline', 'Get', {'namespace': namespace, 'name': name})['pipeline']
                    if pipeline.get('status', {}).get('state') in (3, 'PIPELINE_STATE_READY'): break
                    if time.monotonic() >= deadline: raise RuntimeError('Catalog pipeline is not ready: '+name)
                    time.sleep(2)
                revision = build_revision(pipeline); revision_name = revision['metadata']['name']
                stored = ensure_revision(api, revision)
                native = json.loads(core.run(['kubectl','-n',namespace,'get','revisions.michelangelo.api',
                    revision_name,'--ignore-not-found','-o','json'], capture=True) or 'null')
                # The snapshot contains an already compiled, checksummed READY
                # pipeline. Archived snapshots already retain these stamps.
                if native:
                    if native['metadata']['uid'] != stored['metadata']['uid']:
                        raise RuntimeError('Native revision identity differs from stored snapshot')
                    if (stored.get('status', {}).get('state') not in (3, 'REVISION_STATE_READY') or
                        stored['metadata'].get('annotations', {}).get('michelangelo/Immutable') != 'true'):
                        core.run(['kubectl','-n',namespace,'patch','revisions.michelangelo.api',revision_name,
                            '--subresource=status','--type=merge','-p',json.dumps({'status':{'state':'REVISION_STATE_READY'}})])
                        core.run(['kubectl','-n',namespace,'annotate','revisions.michelangelo.api',revision_name,
                                  'michelangelo/Immutable=true','--overwrite'])
                else:
                    require_ready_archive(stored)
                core.run(['kubectl','-n',namespace,'patch','pipelines.michelangelo.api',name,
                    '--subresource=status','--type=merge','-p',json.dumps({'status':{'latestRevision':{
                        'name':revision_name,'namespace':namespace}}})])
                verified = api('Revision','Get',{'namespace':namespace,'name':revision_name})['revision']
                if verified['spec']['baseResource'] != revision['spec']['baseResource']:
                    raise RuntimeError('Revision source verification failed')
                evidence.append({'pipeline':name,'namespace':namespace,'pipeline_uid':pipeline['metadata']['uid'],
                                 'revision':revision_name,'sha256':revision['metadata']['annotations'][DIGEST]})
            core.save(core.WORK/'pipeline-revision-evidence.json', {'source':SOURCE,'pipelines':evidence})
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
