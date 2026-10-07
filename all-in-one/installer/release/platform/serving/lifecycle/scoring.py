"""Shared batch/online contracts; scoring delegates to the original demo code."""
from __future__ import annotations

import importlib
import math
import json
from pathlib import Path

MODULES = {
    'ranking-smoke': 'train_ranking',
    'ranking-mlops': 'ranking_mlops',
    'ranking-advanced': 'ranking_advanced_mlops',
    'movielens-cf': 'movielens_cf_mlops',
    'fraud-anomaly': 'fraud_anomaly_mlops',
    'housing-synthetic': 'california_housing_mlops',
}
FEATURE_COUNTS = {'ranking-smoke': 4, 'ranking-mlops': 4, 'ranking-advanced': 6,
                  'fraud-anomaly': 5, 'housing-synthetic': 6}
TOLERANCES = {name: {'rtol': 1e-6, 'atol': 1e-7} for name in MODULES}
TOLERANCES.update({'california-housing-xgb': {'rtol': 1e-5, 'atol': 1e-6}, 'bert-cola-finetuning': {'rtol': 1e-5, 'atol': 1e-6}})


def validate_records(demo, model, records):
    if not isinstance(records, list) or not 1 <= len(records) <= 1024:
        raise ValueError('Expected 1–1024 records')
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Each record must be an object')
        if demo == 'movielens-cf':
            for key, vectors in [('user_id', 'user_vectors'), ('item_id', 'item_vectors')]:
                value = record.get(key)
                if type(value) is not int or not 0 <= value < len(model[vectors]):
                    raise ValueError('Unknown or invalid ' + key)
        else:
            features = record.get('features')
            count = FEATURE_COUNTS[demo]
            if not isinstance(features, list) or len(features) != count:
                raise ValueError('Expected exactly ' + str(count) + ' ordered features')
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in features):
                raise ValueError('Features must be finite numbers')
            if demo != 'fraud-anomaly' and features[0] != 1.0:
                raise ValueError('The first feature must be the bias value 1.0')


def load_model(demo, directory):
    directory = Path(directory)
    if demo == 'california-housing-xgb':
        import xgboost
        model = xgboost.Booster(params={'nthread': 2, 'device': 'cpu'})
        model.load_model(directory / 'model.ubj')
        return model
    if demo == 'bert-cola-finetuning':
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(2)
        model = AutoModelForSequenceClassification.from_pretrained(directory / 'model', local_files_only=True).to('cpu').eval()
        tokenizer = AutoTokenizer.from_pretrained(directory / 'model', local_files_only=True)
        return model, tokenizer
    return json.loads((directory / 'model.json').read_text())


def score(demo, model, records):
    if demo == 'california-housing-xgb':
        import numpy as np
        import xgboost
        columns = ['MedInc', 'HouseAge', 'AveRooms', 'AveBedrms', 'Population', 'AveOccup', 'Latitude', 'Longitude']
        if not isinstance(records, list) or not 1 <= len(records) <= 1024:
            raise ValueError('Expected 1–1024 records')
        for record in records:
            features = record.get('features') if isinstance(record, dict) else None
            if not isinstance(features, list) or len(features) != 8 or any(type(x) not in (int, float) or not math.isfinite(x) for x in features):
                raise ValueError('Expected eight finite California Housing features')
        # Match the original Spark float cast before constructing XGBoost DMatrix.
        matrix = np.asarray([r['features'] for r in records], dtype=np.float32)
        if not np.isfinite(matrix).all():
            raise ValueError('Features exceed float32 range')
        names = model.feature_names or columns
        if names != columns or model.num_features() != 8:
            raise ValueError('Incompatible California Housing model revision')
        result = model.predict(xgboost.DMatrix(matrix, feature_names=columns))
        if not np.isfinite(result).all():
            raise ValueError('Invalid predictions')
        return result.tolist()
    if demo == 'bert-cola-finetuning':
        import torch
        if not isinstance(records, list) or not 1 <= len(records) <= 16:
            raise ValueError('Expected 1–16 text records')
        texts = [r.get('text') if isinstance(r, dict) else None for r in records]
        if any(not isinstance(t, str) or not t.strip() or len(t) > 2000 for t in texts):
            raise ValueError('Text must contain 1–2000 characters')
        classifier, tokenizer = model
        inputs = tokenizer(texts, truncation=True, padding='max_length', max_length=128, return_tensors='pt')
        with torch.no_grad():
            logits = classifier(**inputs).logits.cpu()
        if logits.shape != (len(records), 2) or not torch.isfinite(logits).all():
            raise ValueError('Incompatible BERT model output')
        return logits.numpy().astype('float32').tolist()

    if demo not in MODULES:
        raise ValueError('Unsupported model adapter: ' + str(demo))
    if demo in FEATURE_COUNTS:
        weights = model.get('weights')
        if (not isinstance(weights, list) or len(weights) != FEATURE_COUNTS[demo]
                or any(type(w) not in (int, float) or not math.isfinite(w) for w in weights)):
            raise ValueError('Incompatible model feature dimensions or weights')
    validate_records(demo, model, records)
    module = importlib.import_module('oci_demos.' + MODULES[demo])
    if demo in ('ranking-mlops', 'ranking-advanced'):
        result = module.predicted_scores(model, records)
    elif demo == 'ranking-smoke':
        if len(model['weights']) != 4:
            raise ValueError('Incompatible smoke model revision')
        result = [module.sigmoid(sum(w*x for w, x in zip(model['weights'], r['features']))) for r in records]
    elif demo == 'movielens-cf':
        result = [module.score(model, r) for r in records]
    elif demo == 'fraud-anomaly':
        if any(len(model[key]) != 5 for key in ('weights', 'centers', 'mads')) or any(x <= 0 for x in model['mads']):
            raise ValueError('Incompatible fraud model revision')
        result = [module.anomaly_score(model, r) for r in records]
    else:
        result = [module.dot(model['weights'], r['features']) for r in records]
    if len(result) != len(records) or any(not math.isfinite(x) for x in result):
        raise ValueError('Model produced invalid predictions')
    return result
