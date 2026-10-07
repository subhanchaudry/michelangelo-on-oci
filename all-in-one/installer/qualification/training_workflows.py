"""Bounded, real new-site training. No migrated checkpoint is a training result."""
import michelangelo.uniflow.core as uniflow
from michelangelo.uniflow.plugins.ray import RayTask

BERT_REPOSITORY = 'google-bert/bert-base-cased'
BERT_REVISION = 'cd5ef92a9fb2f889e972770a36d4ed042daf221e'
COLA_REPOSITORY = 'nyu-mll/glue'
COLA_REVISION = 'bcdcba79d07bc864c1c254ccfcedcce55bcc9a8c'


def data_snapshot(demo):
    if demo == 'california-housing-xgb':
        from demo_datasets import snapshot
        return snapshot(demo)
    if demo != 'bert-cola-finetuning':
        raise ValueError('Unsupported new-site training model')
    import hashlib
    import io
    from urllib.request import urlopen
    import pyarrow.parquet as parquet
    checksums = {'train': '2e7538afa2000e63f5343f16a758d75c452661a384208399d2035cd2fce45c33',
                 'validation': 'c14b7219a7d9f9fe3dd291fd000f6623ee413805eb108c9c49578ed50873e4ba'}
    def rows(split, count):
        url = f'https://huggingface.co/datasets/{COLA_REPOSITORY}/resolve/{COLA_REVISION}/cola/{split}-00000-of-00001.parquet'
        with urlopen(url, timeout=60) as response:
            payload = response.read(1024*1024+1)
        if len(payload)>1024*1024 or hashlib.sha256(payload).hexdigest()!=checksums[split]:
            raise RuntimeError('Pinned CoLA source file integrity failed')
        selected = parquet.read_table(io.BytesIO(payload), columns=['sentence','label']).slice(0,count).to_pylist()
        if len(selected)!=count:
            raise RuntimeError('Pinned CoLA split is incomplete')
        return [{'text': r['sentence'], 'label': int(r['label'])} for r in selected]
    training, validation = rows('train', 102), rows('validation', 64)
    return {'demo': demo, 'contract_version': 1, 'train': training, 'validation': validation,
            'predict': validation[:8], 'provenance': {'kind': 'GLUE CoLA', 'repository': COLA_REPOSITORY,
             'revision': COLA_REVISION, 'source_sha256': checksums, 'train_selection': 'first 102 source rows',
             'validation_selection': 'first 64 source rows'}}


def fit_xgb(rows, directory):
    import numpy as np
    import xgboost as xgb
    columns = ['MedInc','HouseAge','AveRooms','AveBedrms','Population','AveOccup','Latitude','Longitude']
    matrix = xgb.DMatrix(np.asarray([r['features'] for r in rows], dtype=np.float32),
                         label=[r['target'] for r in rows], feature_names=columns)
    params = {'objective': 'reg:squarederror', 'tree_method': 'hist', 'device': 'cpu',
              'nthread': 1, 'seed': 17, 'max_depth': 4, 'eta': 0.1}
    history = {}
    model = xgb.train(params, matrix, num_boost_round=50, evals=[(matrix, 'train')],
                      evals_result=history, verbose_eval=False)
    model.save_model(directory/'model.ubj')
    losses = history['train']['rmse']
    if len(losses) != 50 or not losses[-1] < losses[0]:
        raise RuntimeError('XGBoost training did not improve its objective')
    return {'framework': 'xgboost', 'versions': {'xgboost': xgb.__version__},
            'settings': params, 'optimizer_steps': 50, 'training_rmse': losses}


def fit_bert(rows, directory):
    import math
    import torch
    import transformers
    transformers.set_seed(17)
    torch.set_num_threads(1)
    tokenizer = transformers.AutoTokenizer.from_pretrained(BERT_REPOSITORY, revision=BERT_REVISION, trust_remote_code=False)
    model = transformers.AutoModelForSequenceClassification.from_pretrained(
        BERT_REPOSITORY, revision=BERT_REVISION, num_labels=2, trust_remote_code=False, use_safetensors=True)
    initial = model.classifier.weight.detach().clone()
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5)
    losses = []
    # Full-parameter CPU fine-tuning, one bounded epoch on the persisted rows.
    for start in range(0, len(rows), 4):
        batch = rows[start:start+4]
        encoded = tokenizer([r['text'] for r in batch], padding='max_length', truncation=True,
                            max_length=128, return_tensors='pt')
        loss = model(**encoded, labels=torch.tensor([r['label'] for r in batch], dtype=torch.long)).loss
        if not math.isfinite(loss.item()):
            raise RuntimeError('Non-finite BERT training loss')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        losses.append(float(loss.item()))
    changed = float((model.classifier.weight.detach()-initial).abs().max().item())
    if not losses or changed <= 0:
        raise RuntimeError('BERT optimizer did not change model weights')
    model.eval()
    model.save_pretrained(directory/'model', safe_serialization=True)
    tokenizer.save_pretrained(directory/'model')
    return {'framework': 'pytorch-transformers', 'versions': {'torch': torch.__version__, 'transformers': transformers.__version__},
            'pretrained_repository': BERT_REPOSITORY, 'pretrained_revision': BERT_REVISION,
            'settings': {'learning_rate': 2e-5, 'epochs': 1, 'batch_size': 4, 'max_length': 128, 'seed': 17},
            'optimizer_steps': len(losses), 'training_loss': losses, 'classifier_max_weight_change': changed}


def train_and_register(demo):
    import gc
    import hashlib
    import json
    from pathlib import Path
    import tempfile
    import storage
    from scoring import load_model
    from training import evaluate
    from runner import quality_gate
    from model_package import build_package
    from registry import model_name, register_candidate
    data = data_snapshot(demo)
    dataset_uri, dataset_sha = storage.put_json('datasets', data)
    with tempfile.TemporaryDirectory(prefix='new-site-training-') as temporary:
        directory = Path(temporary)
        fit = fit_xgb if demo == 'california-housing-xgb' else fit_bert
        training = fit(data['train'], directory)
        gc.collect()
        loaded = load_model(demo, directory)
        metrics = evaluate(demo, loaded, data['validation'])
        del loaded
        gc.collect()
        gate = quality_gate(demo, metrics, metrics)
        training.update(dataset_uri=dataset_uri, dataset_sha256=dataset_sha,
                        training_rows=len(data['train']), validation_rows=len(data['validation']),
                        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        provenance_uri, provenance_sha = storage.put_json('training-provenance', training)
        provenance = {'kind': 'executed-new-site-training', 'uri': provenance_uri, 'sha256': provenance_sha}
        report = {'demo': demo, 'operation': 'training', 'quality_gate': gate, 'metrics': metrics,
                  'dataset_uri': dataset_uri, 'dataset_sha256': dataset_sha, 'training_provenance': provenance}
        evaluation_uri, _ = storage.put_json('evaluations', report)
        identity = hashlib.sha256()
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                identity.update(path.relative_to(directory).as_posix().encode())
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        identity.update(block)
        name = model_name(demo, identity.hexdigest())
        package_uri, package_sha = storage.put_bytes('models', build_package(demo, name, directory), '.tar')
        registered = register_candidate(demo, name, package_uri, package_uri, package_sha,
            dataset_uri, dataset_sha, evaluation_uri, metrics, gate['passed'], '', training['framework'],
            training_provenance=provenance)
        if not registered['model_registered']:
            raise RuntimeError('Trained candidate failed quality gate: '+json.dumps(gate))
        return {'demo': demo, 'operation': 'training', 'lifecycle': registered,
                'dataset_ref': dataset_uri, 'dataset_sha256': dataset_sha,
                'package_uri': package_uri, 'package_sha256': package_sha,
                'training_provenance': provenance, 'optimizer_steps': training['optimizer_steps']}


@uniflow.task(config=RayTask(head_cpu=1, head_memory='4Gi', head_gpu=0,
    worker_cpu=1, worker_memory='8Gi', worker_gpu=0, worker_instances=1,
    runtime_env={'py_modules': ['/app/oci_demos']}), cache_enabled=False)
def execute_binary_training(demo: str) -> dict:
    import sys
    sys.path.insert(0, '/app/oci_lifecycle')
    from runner import capture_run
    return capture_run(lambda: train_and_register(demo), demo, 'training')


@uniflow.workflow()
def california_housing_xgb():
    return execute_binary_training('california-housing-xgb')


@uniflow.workflow()
def bert_cola_finetuning():
    return execute_binary_training('bert-cola-finetuning')
