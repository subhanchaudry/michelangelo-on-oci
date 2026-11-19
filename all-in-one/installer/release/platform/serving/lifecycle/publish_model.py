"""Attach native lifecycle registration to successful, real demo training tasks."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from demo_datasets import snapshot
from model_package import build_package
from registry import model_name, register_candidate
from runner import quality_gate, chunks
from scoring import load_model
from training import evaluate
import storage


def original_json_training_provenance(demo, result, model):
    """Keep the original training lineage separate from lifecycle evaluation data."""
    import importlib
    from scoring import MODULES
    module = importlib.import_module('oci_demos.' + ('train_ranking' if demo == 'ranking-smoke' else MODULES[demo]))
    source_sha = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    if demo == 'ranking-smoke':
        recipe = {'kind': 'deterministic-per-epoch-generator', 'source_sha256': source_sha,
                  'function': 'partial_gradient', 'epochs': len(model['loss_history']),
                  'workers': model['workers'], 'records_per_worker': model['records_per_worker'],
                  'seed_rule': 'epoch * workers + worker', 'epoch_and_worker_start': 0}
        uri, checksum = storage.put_json('training-provenance', recipe)
        return {'kind': 'versioned-generator-recipe', 'uri': uri, 'sha256': checksum, **recipe}
    original_uri = result['artifacts']['raw_data']
    parsed = urlsplit(original_uri)
    if parsed.scheme != 's3' or parsed.netloc != os.environ['AWS_S3_BUCKET']:
        raise ValueError('Original training data must be in the configured bucket')
    response = storage.client().get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip('/'))
    with response['Body'] as body:
        raw = body.read(32 * 1024**2 + 1)
    if not raw or len(raw) > 32 * 1024**2:
        raise ValueError('Original training dataset exceeds the configured size limit')
    rows = json.loads(raw)
    uri, checksum = storage.put_bytes('training-data', raw, '.json')
    split = {'ranking-mlops': 'user numeric id modulo 5 != 0',
             'ranking-advanced': 'user numeric id modulo 5 != 0',
             'movielens-cf': 'item_id modulo 5 != 0',
             'fraud-anomaly': 'first 1440 rows',
             'housing-synthetic': 'first 960 rows'}[demo]
    return {'kind': 'original-training-and-validation-records', 'uri': uri, 'sha256': checksum,
            'original_uri': original_uri, 'rows': len(rows), 'training_split': split,
            'source_sha256': source_sha}


def publish_json_training_result(demo, result):
    uri=result.get('model_uri') or result.get('artifacts',{}).get('model')
    parsed=urlsplit(uri or '')
    if parsed.scheme!='s3' or parsed.netloc!=os.environ['AWS_S3_BUCKET']:
        raise ValueError('Successful training must persist its actual model in the configured bucket')
    response=storage.client().get_object(Bucket=parsed.netloc,Key=parsed.path.lstrip('/'))
    stream=response['Body']
    try:
        if not 0<response.get('ContentLength',0)<=16*1024**2:raise ValueError('Invalid JSON model size')
        raw=stream.read(16*1024**2+1)
    finally:stream.close()
    if len(raw)>16*1024**2:raise ValueError('Model exceeds the configured size limit')
    model=json.loads(raw)
    provenance=original_json_training_provenance(demo,result,model)
    dataset=snapshot(demo)
    dataset_uri,dataset_sha=storage.put_json('datasets',dataset)
    with tempfile.TemporaryDirectory(prefix='register-model-') as temporary:
        directory=Path(temporary);(directory/'model.json').write_text(json.dumps(model,allow_nan=False,sort_keys=True))
        loaded=load_model(demo,directory)
        chunks(demo,loaded,dataset['validation'])
        metrics=evaluate(demo,loaded,dataset['validation'])
        gate=quality_gate(demo,metrics,metrics)
        report={'demo':demo,'operation':'training-registration','quality_gate':gate,'metrics':metrics,
                'dataset_ref':dataset_uri,'dataset_sha256':dataset_sha,'original_model_uri':uri,
                'training_provenance':provenance,'dataset_role':'lifecycle-evaluation-and-retraining-snapshot'}
        evaluation_uri,_=storage.put_json('evaluations',report)
        name=model_name(demo,hashlib.sha256(raw).hexdigest())
        package_uri,checksum=storage.put_bytes('models',build_package(demo,name,directory),'.tar')
        raw_uri,_=storage.put_bytes('raw-models',raw,'.json')
        registered=register_candidate(demo,name,raw_uri,package_uri,checksum,dataset_uri,dataset_sha,evaluation_uri,metrics,gate['passed'],'','python-ray',training_provenance=provenance)
    if not registered['model_registered']:raise RuntimeError('Trained baseline failed the lifecycle quality gate: '+json.dumps(gate))
    return {**result,'lifecycle':{**registered,'dataset_ref':dataset_uri,'dataset_sha256':dataset_sha,'package_uri':package_uri,'package_sha256':checksum}}


def publish_binary_training_result(demo, artifact_uri, dataset_value=None, training_provenance=None):
    """Register an actual checkpoint emitted by the original XGBoost/BERT tasks."""
    if demo not in ('california-housing-xgb','bert-cola-finetuning'):
        raise ValueError('Unsupported binary model family')
    artifact_uri=artifact_uri if artifact_uri.startswith('s3://') else 's3://'+artifact_uri
    parsed=urlsplit(artifact_uri)
    if parsed.scheme!='s3' or parsed.netloc!=os.environ['AWS_S3_BUCKET'] or parsed.query or parsed.fragment:
        raise ValueError('Training checkpoint must be in the configured artifact bucket')
    client=storage.client();key=parsed.path.lstrip('/')
    with tempfile.TemporaryDirectory(prefix='register-binary-model-') as temporary:
        directory=Path(temporary);download=directory/'download';download.mkdir()
        objects=[]
        try:
            head=client.head_object(Bucket=parsed.netloc,Key=key)
            objects=[{'Key':key,'Size':head['ContentLength']}]
        except client.exceptions.ClientError as error:
            if error.response['Error']['Code'] not in ('404','NoSuchKey','NotFound'):raise
            for page in client.get_paginator('list_objects_v2').paginate(Bucket=parsed.netloc,Prefix=key.rstrip('/')+'/'):
                objects.extend(page.get('Contents',[]))
                if len(objects)>100:raise ValueError('Training checkpoint contains too many files')
        # Object Storage directory markers are not checkpoint files.
        objects = [o for o in objects if not o['Key'].endswith('/')]
        if not objects or sum(o['Size'] for o in objects)>1024**3:
            raise ValueError('Missing or oversized training checkpoint')
        for item in objects:
            relative=item['Key'][len(key.rstrip('/')):].lstrip('/') or 'model.ubj'
            path=Path(relative)
            if path.is_absolute() or '..' in path.parts or '\\' in relative:raise ValueError('Unsafe checkpoint path')
            target=download/path;target.parent.mkdir(parents=True,exist_ok=True)
            client.download_file(parsed.netloc,item['Key'],str(target))
        modeldir=directory/'model-artifacts';modeldir.mkdir()
        import shutil
        if demo=='california-housing-xgb':
            checkpoints=list(download.rglob('model.ubj'))
            if len(checkpoints)!=1:raise ValueError('Expected exactly one trained XGBoost checkpoint')
            shutil.copyfile(checkpoints[0],modeldir/'model.ubj')
        else:
            configs=list(download.rglob('config.json'))
            if len(configs)!=1:raise ValueError('Expected exactly one trained BERT checkpoint')
            shutil.copytree(configs[0].parent,modeldir/'model')
        data=dataset_value or snapshot(demo)
        dataset_uri,dataset_sha=storage.put_json('datasets',data)
        loaded=load_model(demo,modeldir);metrics=evaluate(demo,loaded,data['validation']);del loaded
        gate=quality_gate(demo,metrics,metrics)
        if not training_provenance:
            raise ValueError('Exact original training provenance is required')
        report={'demo':demo,'operation':'training-registration','quality_gate':gate,'metrics':metrics,'dataset_ref':dataset_uri,'dataset_sha256':dataset_sha,'original_checkpoint_uri':artifact_uri,'training_provenance':training_provenance,'dataset_role':'lifecycle-evaluation-and-retraining-snapshot'}
        evaluation_uri,_=storage.put_json('evaluations',report)
        identity=hashlib.sha256()
        for path in sorted(modeldir.rglob('*')):
            if path.is_file():
                identity.update(path.relative_to(modeldir).as_posix().encode())
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):identity.update(block)
        name=model_name(demo,identity.hexdigest())
        package_uri,checksum=storage.put_bytes('models',build_package(demo,name,modeldir),'.tar')
        registered=register_candidate(demo,name,artifact_uri,package_uri,checksum,dataset_uri,dataset_sha,evaluation_uri,metrics,gate['passed'],'','xgboost' if demo=='california-housing-xgb' else 'pytorch-transformers',training_provenance=training_provenance)
        if not registered['model_registered']:raise RuntimeError('Trained baseline failed lifecycle quality gate: '+json.dumps(gate))
        return {**registered,'dataset_ref':dataset_uri,'dataset_sha256':dataset_sha,'package_uri':package_uri,'package_sha256':checksum,'original_checkpoint_uri':artifact_uri}
