"""Durable OCI Object Storage ranking MLOps demonstration.

The workload creates deterministic marketplace events, trains two distributed
linear rankers with Ray, evaluates them using ranking metrics, promotes a
champion through an explicit gate, and writes both model and batch-score
artifacts to the OCI Object Storage S3 Compatibility API.

This is deliberately a small, explainable reference workload. It is not a
production recommender system or a substitute for customer data validation.
"""

from __future__ import annotations

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


FEATURE_NAMES = ("bias", "item_quality", "user_affinity", "price_signal")


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 30), -30)))


def dot(weights: list[float], features: list[float]) -> float:
    return sum(weight * feature for weight, feature in zip(weights, features))


def make_events(users: int, items_per_user: int, seed: int) -> list[dict[str, Any]]:
    """Create deterministic click events with a user-grouped ranking target."""
    randomizer = random.Random(seed)
    events: list[dict[str, Any]] = []
    for user in range(users):
        user_affinity = randomizer.random()
        for item in range(items_per_user):
            item_quality = randomizer.random()
            price_signal = randomizer.random()
            latent_score = (
                0.15
                + 1.25 * item_quality
                + 0.95 * user_affinity
                - 0.55 * price_signal
                + randomizer.uniform(-0.20, 0.20)
            )
            clicked = int(latent_score >= 1.10)
            events.append(
                {
                    "user_id": f"user-{user:03d}",
                    "item_id": f"item-{item:03d}",
                    "features": [1.0, item_quality, user_affinity, price_signal],
                    "label": clicked,
                }
            )
    return events


@ray.remote
def gradient_chunk(records: list[dict[str, Any]], weights: list[float]) -> tuple[list[float], float, int]:
    gradient = [0.0] * len(weights)
    loss = 0.0
    for record in records:
        probability = sigmoid(dot(weights, record["features"]))
        error = probability - record["label"]
        for index, feature in enumerate(record["features"]):
            gradient[index] += error * feature
        loss -= record["label"] * math.log(probability + 1e-12) + (
            1 - record["label"]
        ) * math.log(1 - probability + 1e-12)
    return gradient, loss, len(records)


def chunks(records: list[dict[str, Any]], count: int) -> list[list[dict[str, Any]]]:
    size = max(1, math.ceil(len(records) / count))
    return [records[index : index + size] for index in range(0, len(records), size)]


def train_candidate(
    name: str,
    records: list[dict[str, Any]],
    learning_rate: float,
    epochs: int,
    parallelism: int,
) -> dict[str, Any]:
    weights = [0.0] * len(FEATURE_NAMES)
    history: list[float] = []
    partitions = chunks(records, parallelism)
    for _ in range(epochs):
        partials = ray.get([gradient_chunk.remote(partition, weights) for partition in partitions])
        total = sum(partial[2] for partial in partials)
        gradient = [sum(partial[0][index] for partial in partials) / total for index in range(len(weights))]
        weights = [weight - learning_rate * value for weight, value in zip(weights, gradient)]
        history.append(sum(partial[1] for partial in partials) / total)
    return {
        "name": name,
        "weights": weights,
        "learning_rate": learning_rate,
        "epochs": epochs,
        "loss_history": history,
    }


def predicted_scores(model: dict[str, Any], records: list[dict[str, Any]]) -> list[float]:
    return [sigmoid(dot(model["weights"], record["features"])) for record in records]


def auc(records: list[dict[str, Any]], scores: list[float]) -> float:
    positives = sum(record["label"] for record in records)
    negatives = len(records) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    paired = sorted(zip(scores, (record["label"] for record in records)), key=lambda pair: pair[0])
    rank_sum = sum(index + 1 for index, (_, label) in enumerate(paired) if label == 1)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def ndcg_at_k(records: list[dict[str, Any]], scores: list[float], k: int = 10) -> float:
    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for record, score in zip(records, scores):
        grouped[record["user_id"]].append((score, record["label"]))
    values: list[float] = []
    for values_for_user in grouped.values():
        ordered = [label for _, label in sorted(values_for_user, reverse=True)[:k]]
        ideal = sorted((label for _, label in values_for_user), reverse=True)[:k]
        dcg = sum(label / math.log2(index + 2) for index, label in enumerate(ordered))
        ideal_dcg = sum(label / math.log2(index + 2) for index, label in enumerate(ideal))
        values.append(dcg / ideal_dcg if ideal_dcg else 1.0)
    return sum(values) / len(values)


def evaluate(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, float]:
    scores = predicted_scores(model, records)
    return {
        "auc": auc(records, scores),
        "ndcg_at_10": ndcg_at_k(records, scores, 10),
        "log_loss": sum(
            -record["label"] * math.log(score + 1e-12)
            - (1 - record["label"]) * math.log(1 - score + 1e-12)
            for record, score in zip(records, scores)
        )
        / len(records),
    }


def storage_client() -> tuple[Any, str, str]:
    endpoint = os.environ["OCI_S3_ENDPOINT"]
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    return (
        boto3.client("s3", endpoint_url=endpoint, region_name=os.environ["AWS_REGION"]),
        os.environ["OCI_S3_BUCKET"],
        endpoint,
    )


def put_json(client: Any, bucket: str, key: str, content: Any) -> str:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(content, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{bucket}/{key}"


def run() -> None:
    run_id = (os.environ.get("MLOPS_RUN_ID", "ranking-demo-20260902") + "-" + uuid.uuid4().hex[:8])
    users = int(os.environ.get("RANKING_USERS", "80"))
    items_per_user = int(os.environ.get("RANKING_ITEMS_PER_USER", "20"))
    seed = int(os.environ.get("RANKING_SEED", "20260902"))
    min_ndcg = float(os.environ.get("MIN_NDCG_AT_10", "0.80"))
    baseline_ndcg = float(os.environ.get("BASELINE_NDCG_AT_10", "0.70"))
    required_improvement = float(os.environ.get("MIN_NDCG_IMPROVEMENT", "0.02"))
    ray.init(address=os.environ.get("RAY_ADDRESS", "auto"), runtime_env=json.loads(os.environ.get("OCI_DEMO_RAY_RUNTIME_ENV", "{}")))
    client, bucket, endpoint = storage_client()

    all_records = make_events(users, items_per_user, seed)
    train_records = [record for record in all_records if int(record["user_id"].split("-")[1]) % 5 != 0]
    validation_records = [
        record for record in all_records if int(record["user_id"].split("-")[1]) % 5 == 0
    ]
    prefix = f"mlops-demo/personalization-ranking/{run_id}"
    raw_uri = put_json(client, bucket, f"{prefix}/data/raw-events.json", all_records)

    candidates = [
        train_candidate("baseline-linear", train_records, learning_rate=0.05, epochs=3, parallelism=4),
        train_candidate("candidate-linear", train_records, learning_rate=0.20, epochs=12, parallelism=4),
    ]
    for candidate in candidates:
        candidate["metrics"] = evaluate(candidate, validation_records)
    champion = max(candidates, key=lambda candidate: candidate["metrics"]["ndcg_at_10"])
    champion_ndcg = champion["metrics"]["ndcg_at_10"]
    if champion_ndcg < min_ndcg or champion_ndcg < baseline_ndcg + required_improvement:
        raise RuntimeError(
            f"promotion gate failed: ndcg_at_10={champion_ndcg:.4f}, "
            f"required={max(min_ndcg, baseline_ndcg + required_improvement):.4f}"
        )

    model_uri = put_json(client, bucket, f"{prefix}/models/champion.json", champion)
    metrics_uri = put_json(
        client,
        bucket,
        f"{prefix}/metrics/candidate-comparison.json",
        {"run_id": run_id, "candidates": candidates, "promotion_gate": "passed"},
    )
    scoring_records = make_events(20, 15, seed + 1)
    scored = [
        {
            "user_id": record["user_id"],
            "item_id": record["item_id"],
            "ranking_score": sigmoid(dot(champion["weights"], record["features"])),
        }
        for record in scoring_records
    ]
    batch_uri = put_json(client, bucket, f"{prefix}/scores/batch-recommendations.json", scored)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "feature_schema": list(FEATURE_NAMES),
        "training_records": len(train_records),
        "validation_records": len(validation_records),
        "champion": champion["name"],
        "metrics": champion["metrics"],
        "artifacts": {"raw_data": raw_uri, "model": model_uri, "metrics": metrics_uri, "batch_scores": batch_uri},
    }
    manifest_uri = put_json(client, bucket, f"{prefix}/run-manifest.json", manifest)
    # Keep a single machine-readable result line in the Ray submitter log for
    # the promotion helper while preserving the full artifact manifest in S3.
    print("MLOPS_RESULT=" + json.dumps({"status": "promoted", "manifest": manifest_uri, **manifest}, sort_keys=True))


if __name__ == "__main__":
    run()
