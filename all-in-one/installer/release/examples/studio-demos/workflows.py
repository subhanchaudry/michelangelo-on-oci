"""Studio entry points for the OCI synthetic CPU demonstrations.

Tasks execute the same reviewed scripts as the standalone KubeRay demos.
The packaging job reads a content-addressed ConfigMap. Studio tasks fetch the
resulting source archive using the existing private artifact access.
"""
import michelangelo.uniflow.core as uniflow
from michelangelo.uniflow.plugins.ray import RayTask


@uniflow.task(config=RayTask(head_cpu=1, head_memory="4Gi", head_gpu=0,
                           worker_cpu=1, worker_memory="4Gi", worker_gpu=0,
                           worker_instances=1, runtime_env={"py_modules": ["/app/oci_demos"]}), cache_enabled=False)
def execute_demo(demo: str) -> dict:
    import os,sys
    if os.environ.get('MA_REGISTER_LIFECYCLE') == 'true':
        sys.path.insert(0,'/app/oci_lifecycle')
        from runner import capture_run
        return capture_run(lambda: _execute_demo(demo),demo,'training')
    return _execute_demo(demo)


def _execute_demo(demo: str) -> dict:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    def register_lifecycle(result):
        # Enabled only by the lifecycle rollout after its native permissions exist.
        if os.environ.get("MA_REGISTER_LIFECYCLE") == "true":
            sys.path.insert(0, "/app/oci_lifecycle")
            from publish_model import publish_json_training_result
            return publish_json_training_result(demo, result)
        return result

    modules = {
        "ranking-smoke": "train_ranking",
        "ranking-mlops": "ranking_mlops",
        "ranking-advanced": "ranking_advanced_mlops",
        "movielens-cf": "movielens_cf_mlops",
        "fraud-anomaly": "fraud_anomaly_mlops",
        "housing-synthetic": "california_housing_mlops",
    }
    if demo == "controlled-failure":
        raise RuntimeError("intentional ranking demo failure: expected diagnostic demonstration")
    module = modules[demo]
    env = dict(os.environ)
    env.update(OCI_S3_ENDPOINT=os.environ["AWS_ENDPOINT_URL"],
               OCI_S3_BUCKET=os.environ["AWS_S3_BUCKET"],
               MLOPS_RUN_ID="studio-" + demo,
               OCI_DEMO_RAY_RUNTIME_ENV=json.dumps({"py_modules": ["/app/oci_demos"]}),
               PYTHONPATH="/app:" + os.environ.get("PYTHONPATH", ""))
    result = subprocess.run([sys.executable, "-m", "oci_demos." + module],
                            env=env, text=True, capture_output=True, timeout=900)
    print(result.stdout)
    if result.returncode:
        print(result.stderr)
        raise RuntimeError(f"{demo} failed with exit status {result.returncode}")
    records = [json.loads(line.split("=", 1)[1]) for line in result.stdout.splitlines()
               if line.startswith("MLOPS_RESULT=")]
    if records:
        record = records[-1]
        if record["status"] != "promoted":
            raise RuntimeError("Model promotion gate did not pass")
        return register_lifecycle(record)
    artifact = json.loads(Path("/tmp/michelangelo-ranking-model.json").read_text())
    if not artifact["loss_history"][-1] < artifact["loss_history"][0]:
        raise RuntimeError("Ranking smoke loss did not improve")
    persisted = [json.loads(line.split("=", 1)[1]) for line in result.stdout.splitlines()
                 if line.startswith("SMOKE_RESULT=")]
    if not persisted or not persisted[-1].get("model_uri"):
        raise RuntimeError("Studio smoke model was not persisted to Object Storage")
    return register_lifecycle({"status": "success", "demo": demo, "model": artifact, **persisted[-1]})


@uniflow.workflow()
def ranking_smoke():
    return execute_demo("ranking-smoke")


@uniflow.workflow()
def ranking_mlops():
    return execute_demo("ranking-mlops")


@uniflow.workflow()
def ranking_advanced():
    return execute_demo("ranking-advanced")


@uniflow.workflow()
def movielens_cf():
    return execute_demo("movielens-cf")


@uniflow.workflow()
def fraud_anomaly():
    return execute_demo("fraud-anomaly")


@uniflow.workflow()
def housing_synthetic():
    return execute_demo("housing-synthetic")


@uniflow.workflow()
def controlled_failure():
    return execute_demo("controlled-failure")
