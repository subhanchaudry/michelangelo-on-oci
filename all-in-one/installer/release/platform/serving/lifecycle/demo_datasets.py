"""Bounded, deterministic snapshots of the demonstrations' actual input contracts."""
import importlib
import random
from scoring import MODULES


def snapshot(demo):
    provenance={'seed':17,'kind':'synthetic','source':'existing OCI demo generator'}
    if demo=='ranking-smoke':
        rng=random.Random(17);rows=[]
        for _ in range(1280):
            x=[1.0,rng.random(),rng.random(),rng.random()]
            rows.append({'features':x,'label':int(x[1]+0.7*x[2]-0.3*x[3]>0.85)})
        train,validation=rows[:1024],rows[1024:]
    elif demo in MODULES:
        module=importlib.import_module('oci_demos.'+MODULES[demo])
        if demo.startswith('ranking-'):
            train,validation=module.make_events(64,20,17),module.make_events(16,20,18)
        elif demo=='movielens-cf':
            # Hold out pairs while retaining every user and item in training.
            # Twelve held-out items per user make Precision@5 discriminating.
            rows=module.make_interactions(32,60,4,17)
            train=[r for r in rows if (r['user_id']+r['item_id'])%5]
            validation=[r for r in rows if not (r['user_id']+r['item_id'])%5]
        elif demo=='fraud-anomaly':
            train,validation=module.make_transactions(1024,17),module.make_transactions(256,18)
        else:
            train,validation=module.make_records(1024,17),module.make_records(256,18)
    elif demo=='california-housing-xgb':
        from sklearn.datasets import fetch_california_housing
        import numpy as np
        source=fetch_california_housing()
        ids=np.random.RandomState(17).permutation(len(source.target))[:2304]
        rows=[{'features':source.data[i].astype('float32').tolist(),'target':float(source.target[i])} for i in ids]
        train,validation=rows[:2048],rows[2048:]
        provenance={'kind':'California Housing','source':'sklearn.datasets.fetch_california_housing','seed':17,'source_rows':len(source.target),'columns':list(source.feature_names)}
    elif demo=='bert-cola-finetuning':
        # Snapshot text and labels, then all experiments consume only the stored bytes.
        from datasets import load_dataset
        source=load_dataset('nyu-mll/glue','cola')
        train=[{'text':r['sentence'],'label':int(r['label'])} for r in source['train'].select(range(102))]
        validation=[{'text':r['sentence'],'label':int(r['label'])} for r in source['validation'].select(range(64))]
        provenance={'kind':'GLUE CoLA','source':'nyu-mll/glue/cola','train_fingerprint':source['train']._fingerprint,'validation_fingerprint':source['validation']._fingerprint}
    else:raise ValueError('Unsupported dataset')
    return {'demo':demo,'contract_version':1,'provenance':provenance,'train':train,'validation':validation,'predict':validation[:8]}
