"""Typed Studio lifecycle entry points, serialized by the pilot execution queue."""
import michelangelo.uniflow.core as uniflow
from michelangelo.uniflow.plugins.ray import RayTask
from michelangelo.uniflow.core.lib.deployment import create_or_update_deployment, wait_for_deployment

@uniflow.task(config=RayTask(head_cpu=1, head_memory="5Gi", head_gpu=0,
                           worker_cpu=1, worker_memory="4Gi", worker_gpu=0,
                           worker_instances=1, runtime_env={"py_modules": ["/app/oci_demos"]}), cache_enabled=False)
def execute_lifecycle(demo: str, operation: str, model_revision: str, dataset_ref: str,
                      dataset_sha256: str, experiment_settings: str, serving_action: str) -> dict:
    import sys
    sys.path.insert(0, "/app/oci_lifecycle")
    from runner import run
    return run(demo, operation, model_revision=model_revision, dataset_ref=dataset_ref,
               dataset_sha256=dataset_sha256, experiment_settings=experiment_settings, serving_action=serving_action)


@uniflow.workflow()
def native_serving(demo: str, model_revision: str, dataset_ref: str, dataset_sha256: str,
                   experiment_settings: str, serving_action: str):
    if serving_action == 'deactivate':
        return execute_lifecycle(demo, 'serving', model_revision, dataset_ref,
                                 dataset_sha256, experiment_settings, serving_action)
    prepared = execute_lifecycle(demo, 'serving-prepare', model_revision, dataset_ref,
                                 dataset_sha256, experiment_settings, serving_action)
    selected = create_or_update_deployment(namespace=prepared['namespace'],
        deployment_name=prepared['deployment_name'], model_revision_name=prepared['model_revision'])
    wait_for_deployment(namespace=prepared['namespace'], deployment_name=selected['deployment_name'],
        expected_model_revision_name=selected['model_revision_name'], timeout=900, poll=5)
    return execute_lifecycle(demo, 'serving-verify', model_revision, dataset_ref,
                             dataset_sha256, experiment_settings, serving_action)


@uniflow.workflow()
def ranking_smoke_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-smoke', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_smoke_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('ranking-smoke', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_smoke_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-smoke', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_smoke_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-smoke', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_mlops_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-mlops', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_mlops_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('ranking-mlops', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_mlops_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-mlops', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_mlops_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-mlops', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_advanced_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-advanced', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_advanced_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('ranking-advanced', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_advanced_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-advanced', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def ranking_advanced_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('ranking-advanced', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def movielens_cf_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('movielens-cf', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def movielens_cf_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('movielens-cf', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def movielens_cf_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('movielens-cf', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def movielens_cf_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('movielens-cf', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def fraud_anomaly_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('fraud-anomaly', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def fraud_anomaly_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('fraud-anomaly', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def fraud_anomaly_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('fraud-anomaly', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def fraud_anomaly_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('fraud-anomaly', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def housing_synthetic_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('housing-synthetic', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def housing_synthetic_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('housing-synthetic', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def housing_synthetic_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('housing-synthetic', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def housing_synthetic_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('housing-synthetic', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def california_housing_xgb_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('california-housing-xgb', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def california_housing_xgb_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('california-housing-xgb', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def california_housing_xgb_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('california-housing-xgb', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def california_housing_xgb_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('california-housing-xgb', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def bert_cola_finetuning_batch_predict(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('bert-cola-finetuning', 'batch-predict', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def bert_cola_finetuning_serving(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return native_serving('bert-cola-finetuning', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def bert_cola_finetuning_experiment(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('bert-cola-finetuning', 'experiment', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)

@uniflow.workflow()
def bert_cola_finetuning_retrain(model_revision: str = "", dataset_ref: str = "", dataset_sha256: str = "", experiment_settings: str = "", serving_action: str = ""):
    return execute_lifecycle('bert-cola-finetuning', 'retrain', model_revision, dataset_ref, dataset_sha256, experiment_settings, serving_action)
