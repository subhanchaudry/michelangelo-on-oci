"""Finalize candidates only after native success and independently verified artifacts."""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PREFIX = 'michelangelo.ai/'
TERMINAL_FAILURE = {'PIPELINE_RUN_STATE_FAILED', 'PIPELINE_RUN_STATE_KILLED', 'PIPELINE_RUN_STATE_SKIPPED', 4, 5, 6}


class InvalidPublication(ValueError):
    pass


def outputs(run):
    for step in run.get('status', {}).get('steps', []):
        for sub in step.get('subSteps', []):
            if isinstance(sub.get('output'), dict):
                yield sub['output']


def candidate_binding(model):
    """Bind persisted execution evidence to this native identity and exact inputs."""
    meta = model['metadata']
    if not meta.get('uid'):
        raise InvalidPublication('candidate-native-identity-missing')
    annotations = {key: value for key, value in meta.get('annotations', {}).items()
                   if key.startswith(PREFIX) and not key.startswith(PREFIX+'publication-')}
    return {'uid': meta['uid'], 'namespace': meta['namespace'], 'name': meta['name'],
            'spec': model['spec'], 'provenance': annotations}


def verify_candidate(model, run, report, read):
    """Pure policy with an injected, bounded artifact reader; no success fabrication."""
    meta = model['metadata']; ann = meta.get('annotations', {}); spec = model['spec']
    namespace, name = meta['namespace'], meta['name']
    source = spec.get('sourcePipelineRun', {})
    if source != {'name': run['metadata']['name'], 'namespace': namespace} or run['metadata']['namespace'] != namespace:
        raise InvalidPublication('source-run-scope-mismatch')
    if run.get('status', {}).get('state') not in ('PIPELINE_RUN_STATE_SUCCEEDED', 3):
        raise InvalidPublication('source-run-not-successful')
    if not report.get('spec', {}).get('sealed') or report['spec'].get('sourcePipelineRun') != source:
        raise InvalidPublication('evaluation-not-sealed-or-source-mismatch')
    name_ref = spec.get('performanceEvaluationReport', {})
    if name_ref != {'name': report['metadata']['name'], 'namespace': namespace} or report['metadata']['namespace'] != namespace:
        raise InvalidPublication('evaluation-reference-mismatch')
    matching = []
    for result in outputs(run):
        registration = result.get('lifecycle') or result.get('registration') or {}
        if isinstance(registration,dict) and registration.get('model_registered') is True and registration.get('model_revision') == name:
            matching.append(result)
    if not matching:
        raise InvalidPublication('persisted-output-missing-candidate')
    result = matching[-1]
    binding = candidate_binding(model)
    if registration_binding(result) != binding:
        raise InvalidPublication('persisted-output-candidate-binding-mismatch')
    if result.get('source_run') != source['name']:
        raise InvalidPublication('persisted-output-source-mismatch')
    if not spec.get('deployableArtifactUri') or len(spec['deployableArtifactUri']) != 1:
        raise InvalidPublication('package-reference-invalid')
    checked = {}
    def check(uri, checksum, limit):
        if not re.fullmatch(r'[0-9a-f]{64}', checksum or ''):
            raise InvalidPublication('missing-artifact-checksum')
        raw = read(uri, limit)
        if not raw or len(raw) > limit or hashlib.sha256(raw).hexdigest() != checksum:
            raise InvalidPublication('artifact-checksum-mismatch')
        checked[uri] = checksum
        return raw
    check(spec['deployableArtifactUri'][0], ann.get(PREFIX+'package-sha256'), 1024**3)
    dataset = json.loads(check(ann.get(PREFIX+'dataset-uri'), ann.get(PREFIX+'dataset-sha256'), 16*1024**2))
    if not isinstance(dataset,dict) or dataset.get('demo') != ann.get(PREFIX+'demo'):
        raise InvalidPublication('dataset-demo-mismatch')
    if ann.get(PREFIX+'training-provenance'):
        provenance=json.loads(ann[PREFIX+'training-provenance'])
        if not isinstance(provenance,dict):raise InvalidPublication('training-provenance-invalid')
        check(provenance.get('uri'),provenance.get('sha256'),32*1024**2)
    else:
        check(ann.get(PREFIX+'training-dataset-uri'),ann.get(PREFIX+'training-dataset-sha256'),16*1024**2)
    evaluation = json.loads(check(ann.get(PREFIX+'evaluation-uri'), ann.get(PREFIX+'evaluation-sha256'), 16*1024**2))
    if not isinstance(evaluation,dict):raise InvalidPublication('evaluation-invalid')
    gate = evaluation.get('quality_gate') or evaluation.get('decision') or {}
    if not isinstance(gate,dict) or gate.get('passed') is not True or evaluation.get('demo') != ann.get(PREFIX+'demo'):
        raise InvalidPublication('quality-gate-not-passed')
    log = check(result.get('log_uri'), result.get('log_sha256'), 8*1024**2).decode()
    # capture_run appends one complete JSON event after arbitrary task output,
    # including pretty-printed JSON. Only its final record proves completion.
    lines = [line for line in log.splitlines() if line.strip()]
    try:
        completed = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        raise InvalidPublication('persisted-completion-log-invalid') from None
    if not isinstance(completed,dict) or completed.get('event') != 'completed':
        raise InvalidPublication('persisted-completion-log-missing')
    result=completed.get('result',{})
    if not isinstance(result,dict):raise InvalidPublication('completion-log-result-invalid')
    registration = result.get('lifecycle') or result.get('registration') or {}
    if not isinstance(registration,dict) or registration.get('model_revision') != name or registration.get('model_registered') is not True:
        raise InvalidPublication('completion-log-candidate-mismatch')
    if registration.get('candidate_binding') != binding:
        raise InvalidPublication('completion-log-candidate-binding-mismatch')
    return hashlib.sha256(json.dumps(checked, sort_keys=True).encode()).hexdigest()


def registration_binding(result):
    registration = result.get('lifecycle') or result.get('registration') or {}
    return registration.get('candidate_binding') if isinstance(registration, dict) else None


def rpc(kind, action, body):
    token = Path(os.environ.get('MA_API_TOKEN_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/token')).read_text().strip()
    req = Request(os.environ['MA_API_HTTP'].rstrip('/')+'/michelangelo.api.v2.'+kind+'Service/'+action+kind,
        data=json.dumps(body).encode(), headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
        'rpc-service':'ma-apiserver','rpc-encoding':'proto','rpc-caller':'model-publication',
        'context-ttl-ms':'30000','grpc-timeout':'30000m'})
    with urlopen(req, timeout=35) as response:
        return json.load(response)


def project_reader(namespace):
    import boto3
    client = boto3.client('s3', endpoint_url=os.environ['AWS_ENDPOINT_URL'], region_name=os.environ['AWS_REGION'])
    def read(uri, limit):
        parsed = urlsplit(uri or '')
        key = parsed.path.lstrip('/')
        if parsed.scheme != 's3' or parsed.netloc != os.environ['AWS_S3_BUCKET'] or parsed.query or parsed.fragment or not key.startswith('studio-demos/lifecycle/'+namespace+'/') or '..' in key.split('/'):
            raise InvalidPublication('artifact-outside-project')
        response = client.get_object(Bucket=parsed.netloc, Key=key)
        with response['Body'] as stream:
            if not 0 < response.get('ContentLength', 0) <= limit:
                raise InvalidPublication('artifact-size-invalid')
            return stream.read(limit+1)
    return read


def reconcile(model, call=rpc, reader=project_reader):
    meta = model['metadata']; namespace = meta['namespace']; ann = meta.setdefault('annotations', {})
    state = ann.get(PREFIX+'publication-state')
    if state in ('eligible', 'quarantined'):
        return state
    source = model.get('spec', {}).get('sourcePipelineRun', {})
    reason, proof = None, None
    if not source.get('name') or source.get('namespace') != namespace:
        reason = 'source-run-reference-invalid'
    else:
        run = call('PipelineRun', 'Get', {'namespace':namespace,'name':source['name']})['pipelineRun']
        native = run.get('status', {}).get('state')
        if native in TERMINAL_FAILURE:
            reason = 'source-run-failed-or-cancelled'
        elif native not in ('PIPELINE_RUN_STATE_SUCCEEDED', 3):
            return 'pending'
        else:
            try:
                ref = model.get('spec', {}).get('performanceEvaluationReport', {})
                if not ref.get('name') or ref.get('namespace') != namespace:
                    raise InvalidPublication('evaluation-reference-invalid')
                report = call('EvaluationReport','Get',ref)['evaluationReport']
                proof = verify_candidate(model, run, report, reader(namespace))
            except (InvalidPublication, json.JSONDecodeError, UnicodeDecodeError) as error:
                reason = str(error)
            # Network/API failures remain pending and are retried; never promote on error.
    ann[PREFIX+'publication-state'] = 'quarantined' if reason else 'eligible'
    ann[PREFIX+'publication-reason'] = reason or 'source-and-artifacts-verified'
    if proof:
        ann[PREFIX+'publication-proof-sha256'] = proof
    meta.setdefault('labels', {})[PREFIX+'eligible'] = 'false' if reason else 'true'
    call('Model','Update',{'model':model})
    return ann[PREFIX+'publication-state']


def main():
    namespaces = json.loads(os.environ['MA_PUBLICATION_NAMESPACES'])
    while True:
        for namespace in namespaces:
            token = ''
            while True:
                page = rpc('Model','List',{'namespace':namespace,'listOptions':{'limit':100,'continue':token}})['modelList']
                for model in page.get('items', []):
                    if model['metadata'].get('annotations', {}).get(PREFIX+'publication-state') != 'pending':
                        continue
                    try:
                        state = reconcile(model)
                        print(json.dumps({'model':model['metadata']['name'],'namespace':namespace,'publication':state}), flush=True)
                    except Exception as error:
                        print(json.dumps({'model':model['metadata']['name'],'namespace':namespace,'retry':type(error).__name__}), flush=True)
                token = page.get('metadata', {}).get('continue', '')
                if not token:
                    break
        time.sleep(10)

if __name__ == '__main__':
    main()
