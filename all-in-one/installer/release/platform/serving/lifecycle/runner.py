"""Real lifecycle execution. Every output is content-addressed; promotion is separate."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import traceback

import storage
from package import unpack_model
from registry import context, model_name, register_candidate, resolve_revision
from scoring import load_model, score
from training import configurations, evaluate, fit, primary
from model_package import build_package


def dataset(demo, uri, checksum):
    value = json.loads(storage.get_bytes(uri, checksum, max_bytes=16*1024**2))
    if not isinstance(value, dict) or value.get('contract_version') != 1 or value.get('demo') != demo:
        raise ValueError('Dataset contract or demo does not match this workflow')
    for split in ('train', 'validation', 'predict'):
        rows = value.get(split)
        maximum = 128 if demo == 'bert-cola-finetuning' else 4096
        if not isinstance(rows, list) or not 1 <= len(rows) <= maximum or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid or unbounded dataset split: ' + split)
    label = {'ranking-smoke':'label','ranking-mlops':'label','ranking-advanced':'label',
             'movielens-cf':'liked','fraud-anomaly':'confirmed_fraud','housing-synthetic':'median_house_value_index',
             'california-housing-xgb':'target','bert-cola-finetuning':'label'}[demo]
    for split in ('train','validation'):
        for row in value[split]:
            target = row.get(label)
            if type(target) not in (int,float) or not math.isfinite(target):
                raise ValueError('Missing or invalid ground-truth label')
            if 'housing' not in demo and target not in (0,1):
                raise ValueError('Binary label must be zero or one')
    return value


def chunks(demo, model, records):
    size = 16 if demo == 'bert-cola-finetuning' else 1024
    return [p for start in range(0, len(records), size) for p in score(demo, model, records[start:start+size])]


def quality_gate(demo, candidate, baseline):
    """No degradation beyond explicit numerical noise; reject non-finite metrics."""
    candidate_score, baseline_score = primary(demo, candidate), primary(demo, baseline)
    floors = {'ranking-smoke': -0.70, 'ranking-mlops': 0.50, 'ranking-advanced': 0.50,
              'movielens-cf': 0.20, 'fraud-anomaly': 0.60, 'housing-synthetic': -1.0,
              'california-housing-xgb': -1.5, 'bert-cola-finetuning': 0.0}
    checks = {'absolute_quality': candidate_score >= floors[demo],
              'baseline_non_regression': candidate_score + 1e-6 >= baseline_score}
    if demo in ('ranking-advanced', 'bert-cola-finetuning'):
        checks['calibration_non_regression'] = candidate['log_loss'] <= baseline['log_loss'] + 1e-6
    if demo == 'fraud-anomaly':
        checks.update({key + '_floor': candidate[key] >= 0.78 for key in ('precision', 'recall', 'f1')})
        checks['f1_non_regression'] = candidate['f1'] + 1e-6 >= baseline['f1']
        checks['channel_recall_floor'] = all(v['recall'] >= 0.70 for v in candidate['channels'].values())
    return {'passed': all(checks.values()), 'checks': checks, 'candidate_score': candidate_score,
            'baseline_score': baseline_score, 'absolute_floor': floors[demo], 'regression_tolerance': 1e-6}


def execute(demo, operation, model_revision, dataset_ref, dataset_sha256, experiment_settings='', serving_action=''):
    # Validate before executing model code, or making any serving mutation.
    if operation not in ('batch-predict', 'experiment', 'retrain', 'serving', 'serving-prepare', 'serving-verify'):
        raise ValueError('Unknown lifecycle operation')
    if operation == 'serving' and serving_action == 'deactivate':
        from serving import transition
        return transition(demo, serving_action, None, None)
    revision = resolve_revision(demo, model_revision)
    data = dataset(demo, dataset_ref, dataset_sha256)
    package_sha = revision.metadata.annotations.get('michelangelo.ai/package-sha256', '')
    with tempfile.TemporaryDirectory(prefix='lifecycle-') as work:
        work = Path(work)
        archive = work/'baseline.tar'
        archive.write_bytes(storage.get_bytes(revision.spec.deployable_artifact_uri[0], package_sha))
        baseline_dir = unpack_model(archive, work/'baseline', package_sha)/'1'
        metadata = json.loads((baseline_dir/'metadata.json').read_text())
        if metadata.get('demo') != demo or metadata.get('contract_version') != 1:
            raise ValueError('Incompatible model package contract')
        baseline = load_model(demo, baseline_dir)
        # Scoring verifies the same contract for batch and online requests.
        expected = chunks(demo, baseline, data['predict'])
        if operation == 'batch-predict':
            output = {'demo': demo, 'model_revision': model_revision, 'dataset_ref': dataset_ref,
                      'dataset_sha256': dataset_sha256, 'predictions': expected,
                      'validation': {'passed': True, 'records': len(expected), 'contract_version': 1}}
            uri, checksum = storage.put_json('predictions', output)
            return {**output, 'output_uri': uri, 'output_sha256': checksum}
        if operation in ('serving', 'serving-prepare', 'serving-verify'):
            from serving import transition
            phase = {'serving': 'full', 'serving-prepare': 'prepare', 'serving-verify': 'verify'}[operation]
            return transition(demo, serving_action, revision, {'records': data['predict'], 'expected': expected}, phase=phase)
        chunks(demo, baseline, data['train'])
        chunks(demo, baseline, data['validation'])
        baseline_metrics = evaluate(demo, baseline, data['validation'])
        trials = []
        settings = configurations(demo, experiment_settings)
        for index, config in enumerate(settings):
            directory = work/('candidate-'+str(index))
            candidate = fit(demo, data['train'], config, directory, baseline_dir)
            metrics = evaluate(demo, candidate, data['validation'])
            trials.append({'index': index, 'settings': config, 'metrics': metrics,
                           'gate': quality_gate(demo, metrics, baseline_metrics)})
            del candidate
        # Rank eligible trials first; tied ranking/AUC must not hide bad calibration
        # or an unusable fraud decision threshold.
        selected = max(trials, key=lambda trial: (trial['gate']['passed'], primary(demo, trial['metrics']),
                       trial['metrics'].get('f1', -trial['metrics'].get('log_loss', 0))))
        report = {'demo': demo, 'operation': operation, 'baseline_revision': model_revision,
                  'baseline_metrics': baseline_metrics, 'dataset_ref': dataset_ref,
                  'dataset_sha256': dataset_sha256, 'trials': trials,
                  'selection': selected['index'], 'decision': selected['gate'],
                  'live_endpoint_changed': False}
        report_uri, report_sha = storage.put_json('evaluations', report)
        result = {'evaluation_uri': report_uri, 'evaluation_sha256': report_sha, **report}
        if operation == 'experiment':
            name = model_name(demo, report_sha)
            result['registration'] = register_candidate(
                demo, name, '', '', '', dataset_ref, dataset_sha256, report_uri,
                selected['metrics'], selected['gate']['passed'], model_revision,
                revision.spec.training_framework, register_model=False)
        if operation == 'retrain':
            directory = work/('candidate-'+str(selected['index']))
            raw_identity = hashlib.sha256()
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    raw_identity.update(path.relative_to(directory).as_posix().encode())
                    with path.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024*1024), b''): raw_identity.update(block)
            name = model_name(demo, raw_identity.hexdigest())
            package = build_package(demo, name, directory)
            package_uri, checksum = storage.put_bytes('models', package, '.tar')
            result['registration'] = register_candidate(
                demo, name, package_uri, package_uri, checksum, dataset_ref, dataset_sha256,
                report_uri, selected['metrics'], selected['gate']['passed'], model_revision,
                revision.spec.training_framework)
        return result


def run(demo, operation, **inputs):
    """Persist success/failure logs and results; never turn a failed run into success."""
    return capture_run(lambda: execute(demo, operation, **inputs), demo, operation)


def capture_run(execute_operation, demo, operation):
    """Persist training and lifecycle task output through the same private log path."""
    namespace, source_run = context()
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            print(json.dumps({'event': 'started', 'demo': demo, 'operation': operation, 'project': namespace, 'source_run': source_run}))
            result = execute_operation()
            print(json.dumps({'event': 'completed', 'result': result}, allow_nan=False))
    except Exception:
        traceback.print_exc(file=log)
        try:
            uri, checksum = storage.publish_log(source_run, log.getvalue())
            print('LIFECYCLE_LOG='+json.dumps({'uri': uri, 'sha256': checksum}))
        finally:
            print(log.getvalue())
        raise
    uri, checksum = storage.publish_log(source_run, log.getvalue())
    print(log.getvalue())
    return {**result, 'log_uri': uri, 'log_sha256': checksum, 'source_run': source_run}
