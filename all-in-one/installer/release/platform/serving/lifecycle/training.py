"""Bounded candidate fitting using the demos' original model implementations."""
from __future__ import annotations
import importlib
import math
import json
from pathlib import Path
from scoring import score, MODULES

DEFAULTS={
 'ranking-smoke':[{'learning_rate':0.15,'epochs':8},{'learning_rate':0.25,'epochs':12}],
 'ranking-mlops':[{'learning_rate':0.3,'epochs':30},{'learning_rate':0.4,'epochs':50}],
 'ranking-advanced':[{'learning_rate':0.15,'epochs':12,'l2':0.001},{'learning_rate':0.25,'epochs':20,'l2':0.001}],
 'movielens-cf':[{'learning_rate':0.05,'epochs':8,'dimensions':4,'regularization':0.01},{'learning_rate':0.1,'epochs':12,'dimensions':8,'regularization':0.01}],
 'fraud-anomaly':[{'threshold':12.5,'weights':[1,1,1,1,1]},{'threshold':14.0,'weights':[1,1,1,1,1]}],
 'housing-synthetic':[{'learning_rate':0.16,'epochs':300,'l2':0.002},{'learning_rate':0.18,'epochs':450,'l2':0.005}],
 'california-housing-xgb':[{'max_depth':3,'eta':0.1,'rounds':10},{'max_depth':4,'eta':0.15,'rounds':15}],
 'bert-cola-finetuning':[{'learning_rate':0.00002,'epochs':1},{'learning_rate':0.00003,'epochs':1}],
}


def configurations(demo, settings):
    values=json.loads(settings) if settings else DEFAULTS[demo]
    if not isinstance(values,list) or len(values)!=2: raise ValueError('Exactly two experiment configurations are required')
    allowed=set(DEFAULTS[demo][0])
    bounds={'learning_rate':(0.000001,0.5),'epochs':(1,80),'l2':(0,0.1),'dimensions':(2,16),'regularization':(0,0.1),'threshold':(0.1,20),'max_depth':(1,6),'eta':(0.01,0.5),'rounds':(1,20)}
    if demo=='housing-synthetic':bounds['epochs']=(1,500)
    for config in values:
        if not isinstance(config,dict) or set(config)!=allowed: raise ValueError('Unexpected or missing experiment settings')
        for key,value in config.items():
            if key=='weights':
                if not isinstance(value,list) or len(value)!=5 or any(type(x) not in (float,int) or not 0.01<=x<=5 for x in value): raise ValueError('Invalid fraud weights')
            elif type(value) not in (int,float) or not math.isfinite(value) or not bounds[key][0]<=value<=bounds[key][1]:
                raise ValueError('Experiment setting outside CPU limits: '+key)
            elif key in ('epochs','dimensions','max_depth','rounds') and type(value) is not int:
                raise ValueError('Integer experiment setting required: '+key)
        if demo=='bert-cola-finetuning' and (config['epochs']!=1 or config['learning_rate']>0.0001):
            raise ValueError('BERT example is limited to one epoch at learning rate <=0.0001')
    return values


def evaluate(demo, model, records):
    if demo in ('california-housing-xgb','bert-cola-finetuning','ranking-smoke'):
        predictions=[]
        size=16 if demo=='bert-cola-finetuning' else 1024
        for start in range(0,len(records),size): predictions.extend(score(demo,model,records[start:start+size]))
        if demo=='california-housing-xgb':
            return {'rmse':math.sqrt(sum((p-float(r['target']))**2 for p,r in zip(predictions,records))/len(records))}
        if demo=='bert-cola-finetuning':
            labels=[int(r['label']) for r in records]; predicted=[int(p[1]>p[0]) for p in predictions]
            tp=sum(a==b==1 for a,b in zip(labels,predicted));tn=sum(a==b==0 for a,b in zip(labels,predicted))
            fp=sum(a==0 and b==1 for a,b in zip(labels,predicted));fn=sum(a==1 and b==0 for a,b in zip(labels,predicted))
            denominator=math.sqrt((tp+fp)*(tp+fn)*(tn+fp)*(tn+fn))
            losses=[]
            for logits,label in zip(predictions,labels):
                maximum=max(logits)
                losses.append(maximum+math.log(sum(math.exp(v-maximum) for v in logits))-logits[label])
            return {'mcc':(tp*tn-fp*fn)/denominator if denominator else 0.0,'accuracy':sum(a==b for a,b in zip(labels,predicted))/len(labels),'log_loss':sum(losses)/len(losses)}
        return {'log_loss':-sum(r['label']*math.log(p+1e-12)+(1-r['label'])*math.log(1-p+1e-12) for p,r in zip(predictions,records))/len(records)}
    module=importlib.import_module('oci_demos.'+MODULES[demo])
    return module.metrics(model,records) if demo=='fraud-anomaly' else module.evaluate(model,records)


def primary(demo, metrics):
    key={'ranking-smoke':'log_loss','ranking-mlops':'ndcg_at_10','ranking-advanced':'ndcg_at_10','movielens-cf':'precision_at_5','fraud-anomaly':'auc','housing-synthetic':'rmse','california-housing-xgb':'rmse','bert-cola-finetuning':'mcc'}[demo]
    value=float(metrics[key])
    if not math.isfinite(value): raise ValueError('Non-finite quality metric')
    return -value if key in ('rmse','log_loss') else value


def fit(demo, records, config, directory, baseline_directory):
    import ray
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if demo=='ranking-smoke':
        module=importlib.import_module('oci_demos.train_ranking')
        model=module.fit_records(records,**config)
    elif demo=='california-housing-xgb':
        import numpy as np
        import xgboost
        columns=['MedInc','HouseAge','AveRooms','AveBedrms','Population','AveOccup','Latitude','Longitude']
        matrix=xgboost.DMatrix(np.asarray([r['features'] for r in records],dtype=np.float32),label=[r['target'] for r in records],feature_names=columns)
        params={'objective':'reg:squarederror','tree_method':'hist','device':'cpu','nthread':2,'seed':17,**{k:v for k,v in config.items() if k!='rounds'}}
        model=xgboost.train(params,matrix,num_boost_round=config['rounds']);model.save_model(directory/'model.ubj')
        return model
    elif demo=='bert-cola-finetuning':
        import torch
        from scoring import load_model
        torch.manual_seed(17);model,tokenizer=load_model(demo,baseline_directory)
        model.train();optimizer=torch.optim.AdamW(model.parameters(),lr=config['learning_rate'])
        for start in range(0,len(records),8):
            rows=records[start:start+8]
            encoded=tokenizer([r['text'] for r in rows],padding='max_length',truncation=True,max_length=128,return_tensors='pt')
            loss=model(**encoded,labels=torch.tensor([r['label'] for r in rows],dtype=torch.long)).loss
            optimizer.zero_grad();loss.backward();optimizer.step()
        model.eval();model.save_pretrained(directory/'model');tokenizer.save_pretrained(directory/'model')
        return model,tokenizer
    else:
        module=importlib.import_module('oci_demos.'+MODULES[demo])
        if demo=='ranking-mlops': model=module.train_candidate('candidate',records,parallelism=2,**config)
        elif demo=='ranking-advanced': model=ray.get(module.fit_candidate.remote('candidate',records,**config))
        elif demo=='movielens-cf': model=ray.get(module.fit_factorization.remote('candidate',records,users=max(r['user_id'] for r in records)+1,items=max(r['item_id'] for r in records)+1,seed=29,**config))
        elif demo=='fraud-anomaly': model=ray.get(module.fit_candidate.remote('candidate',records,**config))
        else: model=ray.get(module.fit_regressor.remote('candidate',records,**config))
    (directory/'model.json').write_text(json.dumps(model,sort_keys=True,allow_nan=False))
    return model
