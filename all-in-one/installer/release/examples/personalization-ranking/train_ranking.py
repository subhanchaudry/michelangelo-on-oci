"""Small distributed ranking demo for the OCI Michelangelo accelerator.

It trains a binary click-through-rate ranker on deterministic synthetic data.
The program has no framework dependency beyond Ray, so it is safe to use as a
cluster smoke test before connecting a customer feature source.
"""
import hashlib
import uuid
import json
import math
import os
import random
from pathlib import Path

import ray


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 30), -30)))


@ray.remote
def partial_gradient(seed: int, weights: list[float], records: int) -> tuple[list[float], float]:
    random.seed(seed)
    gradient = [0.0] * len(weights)
    loss = 0.0
    for _ in range(records):
        features = [1.0, random.random(), random.random(), random.random()]
        target = 1.0 if features[1] + 0.7 * features[2] - 0.3 * features[3] > 0.85 else 0.0
        probability = sigmoid(sum(w * x for w, x in zip(weights, features)))
        error = probability - target
        for index, feature in enumerate(features):
            gradient[index] += error * feature
        loss += -(target * math.log(probability + 1e-9) + (1 - target) * math.log(1 - probability + 1e-9))
    return gradient, loss



def fit_records(records, learning_rate=0.25, epochs=8):
    """Fit the same smoke logistic ranker against a fixed versioned dataset."""
    weights = [0.0] * 4
    history = []
    for _ in range(epochs):
        gradient = [0.0] * 4
        loss = 0.0
        for record in records:
            features, target = record['features'], record['label']
            probability = sigmoid(sum(w*x for w,x in zip(weights,features)))
            for i, feature in enumerate(features):
                gradient[i] += (probability-target)*feature
            loss -= target*math.log(probability+1e-9)+(1-target)*math.log(1-probability+1e-9)
        weights = [w-learning_rate*g/len(records) for w,g in zip(weights,gradient)]
        history.append(loss/len(records))
    return {'model':'synthetic-personalization-ranker','weights':weights,'loss_history':history}


def main() -> None:
    ray.init(address=os.environ.get("RAY_ADDRESS", "auto"), runtime_env=json.loads(os.environ.get("OCI_DEMO_RAY_RUNTIME_ENV", "{}")))
    workers = int(os.environ.get("RANKING_WORKERS", "4"))
    records_per_worker = int(os.environ.get("RANKING_RECORDS_PER_WORKER", "5000"))
    epochs = int(os.environ.get("RANKING_EPOCHS", "8"))
    learning_rate = float(os.environ.get("RANKING_LEARNING_RATE", "0.25"))
    weights = [0.0, 0.0, 0.0, 0.0]
    history = []

    for epoch in range(epochs):
        parts = ray.get([
            partial_gradient.remote(epoch * workers + worker, weights, records_per_worker)
            for worker in range(workers)
        ])
        gradient = [sum(part[0][index] for part in parts) / (workers * records_per_worker) for index in range(4)]
        weights = [weight - learning_rate * value for weight, value in zip(weights, gradient)]
        history.append(sum(part[1] for part in parts) / (workers * records_per_worker))

    artifact = {
        "model": "synthetic-personalization-ranker",
        "weights": weights,
        "loss_history": history,
        "workers": workers,
        "records_per_worker": records_per_worker,
    }
    output = Path(os.environ.get("MODEL_OUTPUT", "/tmp/michelangelo-ranking-model.json"))
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    summary = {"status": "success", "artifact": str(output), "final_loss": history[-1]}
    # Standalone smoke remains usable without storage; Studio always supplies it.
    endpoint = os.environ.get("OCI_S3_ENDPOINT") or os.environ.get("AWS_ENDPOINT_URL")
    bucket = os.environ.get("OCI_S3_BUCKET") or os.environ.get("AWS_S3_BUCKET")
    if endpoint and bucket:
        import boto3
        body = output.read_bytes()
        checksum = hashlib.sha256(body).hexdigest()
        run_id = os.environ.get("MLOPS_RUN_ID", "ranking-smoke") + "-" + uuid.uuid4().hex
        key = "studio-demos/models/ranking-smoke/" + run_id + "/model.json"
        storage = boto3.client("s3", endpoint_url=endpoint if endpoint.startswith(("https://", "http://")) else "https://" + endpoint,
                               region_name=os.environ["AWS_REGION"])
        storage.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json",
                           Metadata={"sha256": checksum})
        restored = storage.get_object(Bucket=bucket, Key=key)["Body"]
        try:
            if hashlib.sha256(restored.read()).hexdigest() != checksum:
                raise RuntimeError("Persisted smoke model checksum mismatch")
        finally:
            restored.close()
        summary.update(model_uri="s3://" + bucket + "/" + key, model_sha256=checksum)
    print("SMOKE_RESULT=" + json.dumps(summary, sort_keys=True))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
