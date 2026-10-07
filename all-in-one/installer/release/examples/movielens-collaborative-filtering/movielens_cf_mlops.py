"""CPU-only MovieLens-style collaborative-filtering MLOps demo for OCI.

Inspired by Michelangelo AI's upstream MovieLens example, this implementation
uses a compact pure-Python matrix factorization trainer so it runs in the
existing pinned OCI playground image without adding PyTorch or downloading the
MovieLens dataset.  The generated interaction data is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import boto3
import ray


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 30), -30)))


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def make_interactions(users: int, items: int, dimensions: int, seed: int) -> list[dict[str, Any]]:
    generator = random.Random(seed)
    true_users = [[generator.uniform(-1, 1) for _ in range(dimensions)] for _ in range(users)]
    true_items = [[generator.uniform(-1, 1) for _ in range(dimensions)] for _ in range(items)]
    interactions: list[dict[str, Any]] = []
    for user in range(users):
        for item in range(items):
            score = 0.32 + dot(true_users[user], true_items[item]) + generator.uniform(-0.25, 0.25)
            interactions.append(
                {
                    "interaction_id": f"rating-{user:03d}-{item:03d}",
                    "user_id": user,
                    "item_id": item,
                    "genre": ("drama", "comedy", "action", "documentary")[item % 4],
                    "liked": int(sigmoid(score) >= 0.57),
                }
            )
    return interactions


def quality_report(records: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    required = {"interaction_id", "user_id", "item_id", "genre", "liked"}
    ids = [record["interaction_id"] for record in records]
    likes = sum(record["liked"] for record in records)
    genres = sorted({record["genre"] for record in records})
    checks = {
        "record_count_matches": len(records) == expected,
        "unique_interaction_ids": len(ids) == len(set(ids)),
        "binary_implicit_labels": all(record["liked"] in (0, 1) for record in records),
        "expected_genres_present": len(genres) == 4,
        "label_rate_reasonable": 0.05 < likes / len(records) < 0.95,
        "all_required_fields_present": all(required <= set(record) for record in records),
    }
    fingerprint = hashlib.sha256(
        json.dumps({"fields": sorted(required), "genres": genres}, sort_keys=True).encode()
    ).hexdigest()
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "record_count": len(records),
        "positive_rate": likes / len(records),
        "genres": genres,
        "schema_fingerprint": fingerprint,
        "checks": checks,
    }


@ray.remote
def fit_factorization(
    name: str,
    records: list[dict[str, Any]],
    users: int,
    items: int,
    dimensions: int,
    learning_rate: float,
    epochs: int,
    regularization: float,
    seed: int,
) -> dict[str, Any]:
    generator = random.Random(seed)
    user_vectors = [[generator.uniform(-0.1, 0.1) for _ in range(dimensions)] for _ in range(users)]
    item_vectors = [[generator.uniform(-0.1, 0.1) for _ in range(dimensions)] for _ in range(items)]
    loss_history: list[float] = []
    local_records = list(records)
    for _ in range(epochs):
        generator.shuffle(local_records)
        total_loss = 0.0
        for record in local_records:
            user, item, label = record["user_id"], record["item_id"], record["liked"]
            score = sigmoid(dot(user_vectors[user], item_vectors[item]))
            error = score - label
            previous_user = list(user_vectors[user])
            previous_item = list(item_vectors[item])
            for index in range(dimensions):
                user_vectors[user][index] -= learning_rate * (error * previous_item[index] + regularization * previous_user[index])
                item_vectors[item][index] -= learning_rate * (error * previous_user[index] + regularization * previous_item[index])
            total_loss += -label * math.log(score + 1e-12) - (1 - label) * math.log(1 - score + 1e-12)
        loss_history.append(total_loss / len(local_records))
    return {
        "name": name,
        "dimensions": dimensions,
        "learning_rate": learning_rate,
        "epochs": epochs,
        "regularization": regularization,
        "user_vectors": user_vectors,
        "item_vectors": item_vectors,
        "loss_history": loss_history,
        "final_training_loss": loss_history[-1],
    }


def score(model: dict[str, Any], record: dict[str, Any]) -> float:
    return sigmoid(dot(model["user_vectors"][record["user_id"]], model["item_vectors"][record["item_id"]]))


def evaluate(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    losses: list[float] = []
    for record in records:
        prediction = score(model, record)
        label = record["liked"]
        losses.append(-label * math.log(prediction + 1e-12) - (1 - label) * math.log(1 - prediction + 1e-12))
        grouped[record["user_id"]].append((prediction, label, record["item_id"]))
    precision_values, recall_values, recommended_items = [], [], set()
    for recommendations in grouped.values():
        top = sorted(recommendations, reverse=True)[:5]
        hits = sum(label for _, label, _ in top)
        positives = sum(label for _, label, _ in recommendations)
        precision_values.append(hits / 5)
        recall_values.append(hits / positives if positives else 1.0)
        recommended_items.update(item for _, _, item in top)
    return {
        "log_loss": sum(losses) / len(losses),
        "precision_at_5": sum(precision_values) / len(precision_values),
        "recall_at_5": sum(recall_values) / len(recall_values),
        "catalog_coverage": len(recommended_items) / len(model["item_vectors"]),
    }


def storage_client() -> tuple[Any, str]:
    endpoint = os.environ["OCI_S3_ENDPOINT"]
    endpoint = endpoint if endpoint.startswith("http") else f"https://{endpoint}"
    return boto3.client("s3", endpoint_url=endpoint, region_name=os.environ["AWS_REGION"]), os.environ["OCI_S3_BUCKET"]


def put_json(client: Any, bucket: str, key: str, value: Any) -> str:
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(value, indent=2, sort_keys=True).encode(), ContentType="application/json")
    return f"s3://{bucket}/{key}"


def recommendations(model: dict[str, Any], users: int, items: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for user_id in range(min(users, 12)):
        scored = [(sigmoid(dot(model["user_vectors"][user_id], model["item_vectors"][item_id])), item_id) for item_id in range(items)]
        for rank, (prediction, item_id) in enumerate(sorted(scored, reverse=True)[:5], 1):
            output.append({
                "user_id": f"viewer-{user_id:03d}", "rank": rank, "movie_id": f"movie-{item_id:03d}",
                "genre": ("drama", "comedy", "action", "documentary")[item_id % 4],
                "relevance_score": prediction, "explanation": "collaborative latent-preference match",
            })
    return output


def run() -> None:
    run_id = (os.environ.get("MLOPS_RUN_ID", "movielens-cf-20260902") + "-" + uuid.uuid4().hex[:8])
    users, items, dimensions, seed = 100, 60, 4, 20260902
    ray.init(address=os.environ.get("RAY_ADDRESS", "auto"), runtime_env=json.loads(os.environ.get("OCI_DEMO_RAY_RUNTIME_ENV", "{}")))
    client, bucket = storage_client()
    prefix = f"mlops-demo/movielens-collaborative-filtering/{run_id}"
    records = make_interactions(users, items, dimensions, seed)
    quality = quality_report(records, users * items)
    raw_uri = put_json(client, bucket, f"{prefix}/data/synthetic-interactions.json", records)
    quality_uri = put_json(client, bucket, f"{prefix}/quality/data-quality-report.json", quality)
    contract = {"entity_keys": ["user_id", "item_id"], "label": "liked", "embedding_dimensions": dimensions, "schema_fingerprint": quality["schema_fingerprint"]}
    contract_uri = put_json(client, bucket, f"{prefix}/features/interaction-contract.json", contract)
    if quality["status"] != "passed":
        raise RuntimeError("interaction data quality gate failed")
    train = [record for record in records if record["item_id"] % 5 != 0]
    validation = [record for record in records if record["item_id"] % 5 == 0]
    # All trials share an initialization seed so the leaderboard compares
    # hyperparameters rather than random initial embeddings. The baseline is
    # deliberately under-trained; the other candidates demonstrate the gate.
    configurations = [
        ("mf-baseline", 0.003, 1, 0.005),
        ("mf-balanced", 0.018, 5, 0.005),
        ("mf-regularized", 0.025, 8, 0.020),
    ]
    candidates = ray.get([fit_factorization.remote(name, train, users, items, dimensions, lr, epochs, reg, seed) for name, lr, epochs, reg in configurations])
    for candidate in candidates:
        candidate["metrics"] = evaluate(candidate, validation)
    baseline = candidates[0]
    # Select the best-calibrated model only after it meets strong retrieval
    # floors. This avoids choosing a trivially overconfident model solely on a
    # small Precision@5 advantage.
    eligible = [
        candidate for candidate in candidates
        if candidate["metrics"]["precision_at_5"] >= 0.85
        and candidate["metrics"]["recall_at_5"] >= 0.70
    ]
    champion = min(eligible, key=lambda candidate: candidate["metrics"]["log_loss"])
    gate = {
        "minimum_precision_at_5": 0.85, "minimum_recall_at_5": 0.70, "minimum_catalog_coverage": 0.15,
        "minimum_log_loss_reduction": 0.01,
        "actual_precision_at_5": champion["metrics"]["precision_at_5"], "actual_recall_at_5": champion["metrics"]["recall_at_5"],
        "actual_catalog_coverage": champion["metrics"]["catalog_coverage"], "actual_log_loss_reduction": baseline["metrics"]["log_loss"] - champion["metrics"]["log_loss"],
    }
    gate["passed"] = all((gate["actual_precision_at_5"] >= gate["minimum_precision_at_5"], gate["actual_recall_at_5"] >= gate["minimum_recall_at_5"], gate["actual_catalog_coverage"] >= gate["minimum_catalog_coverage"], gate["actual_log_loss_reduction"] >= gate["minimum_log_loss_reduction"]))
    leaderboard_uri = put_json(client, bucket, f"{prefix}/experiments/leaderboard.json", {"run_id": run_id, "candidates": candidates, "promotion_gate": gate})
    if not gate["passed"]:
        raise RuntimeError("collaborative-filtering promotion gate failed")
    model_uri = put_json(client, bucket, f"{prefix}/models/champion.json", champion)
    reloaded = json.loads(client.get_object(Bucket=bucket, Key=f"{prefix}/models/champion.json")["Body"].read().decode())
    reload_validation = {"artifact_uri": model_uri, "status": "passed" if reloaded == champion else "failed", "weights_match": reloaded == champion}
    reload_uri = put_json(client, bucket, f"{prefix}/validation/model-reload.json", reload_validation)
    if reload_validation["status"] != "passed":
        raise RuntimeError("model reload validation failed")
    batch_uri = put_json(client, bucket, f"{prefix}/scores/top-five-recommendations.json", recommendations(champion, users, items))
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "execution_engine": "KubeRay on OCI OKE", "training_records": len(train), "validation_records": len(validation), "candidate_count": len(candidates), "champion": champion["name"], "metrics": champion["metrics"], "promotion_gate": gate, "artifacts": {"raw_data": raw_uri, "quality_report": quality_uri, "feature_contract": contract_uri, "leaderboard": leaderboard_uri, "model": model_uri, "model_reload_validation": reload_uri, "batch_scores": batch_uri}}
    manifest_uri = put_json(client, bucket, f"{prefix}/run-manifest.json", manifest)
    print("MLOPS_RESULT=" + json.dumps({"status": "promoted", "run_id": run_id, "champion": champion["name"], "metrics": champion["metrics"], "promotion_gate": gate, "artifacts": {**manifest["artifacts"], "manifest": manifest_uri}}, sort_keys=True))


if __name__ == "__main__":
    run()
