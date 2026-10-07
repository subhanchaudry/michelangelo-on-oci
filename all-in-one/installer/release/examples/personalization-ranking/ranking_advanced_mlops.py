"""Advanced, artifact-backed ranking MLOps demonstration for the OCI playground.

The demo is intentionally self-contained: it creates deterministic marketplace
interaction data, validates a lightweight feature contract, evaluates several
Ray-distributed candidate rankers, applies both global and segment promotion
gates, verifies that the promoted model can be read back from OCI Object
Storage, and produces explainable batch recommendations.

It demonstrates an operating pattern, not a production recommendation model.
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


FEATURE_NAMES = (
    "bias",
    "item_quality",
    "user_affinity",
    "price_value",
    "context_match",
    "freshness",
)
SEGMENTS = ("consumer-mobile", "consumer-web", "business-mobile", "business-web")


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 30), -30)))


def dot(weights: list[float], features: list[float]) -> float:
    return sum(weight * feature for weight, feature in zip(weights, features))


def event_segment(user: int) -> str:
    audience = "business" if user % 3 == 0 else "consumer"
    channel = "mobile" if user % 2 == 0 else "web"
    return f"{audience}-{channel}"


def make_events(users: int, items_per_user: int, seed: int) -> list[dict[str, Any]]:
    """Create deterministic, user-grouped ranking events with useful context."""
    randomizer = random.Random(seed)
    events: list[dict[str, Any]] = []
    for user in range(users):
        segment = event_segment(user)
        user_affinity = randomizer.random()
        for item in range(items_per_user):
            item_quality = randomizer.random()
            price_value = randomizer.random()
            context_match = randomizer.random()
            freshness = randomizer.random()
            business_adjustment = 0.13 if segment.startswith("business") else 0.0
            mobile_adjustment = 0.08 if segment.endswith("mobile") else -0.03
            latent_score = (
                -0.72
                + 1.45 * item_quality
                + 0.92 * user_affinity
                + 0.65 * price_value
                + 0.82 * context_match
                + 0.36 * freshness
                + business_adjustment
                + mobile_adjustment
                + randomizer.uniform(-0.28, 0.28)
            )
            clicked = int(sigmoid(latent_score) >= 0.67)
            events.append(
                {
                    "event_id": f"evt-{user:04d}-{item:03d}",
                    "user_id": f"user-{user:04d}",
                    "item_id": f"item-{item:03d}",
                    "segment": segment,
                    "features": [
                        1.0,
                        item_quality,
                        user_affinity,
                        price_value,
                        context_match,
                        freshness,
                    ],
                    "label": clicked,
                }
            )
    return events


def quality_report(records: list[dict[str, Any]], expected_records: int) -> dict[str, Any]:
    required = {"event_id", "user_id", "item_id", "segment", "features", "label"}
    missing_fields = sorted(
        required - set().union(*(set(record.keys()) for record in records))
    )
    invalid_features = sum(
        1
        for record in records
        if len(record.get("features", [])) != len(FEATURE_NAMES)
        or any(not isinstance(value, (int, float)) for value in record.get("features", []))
    )
    invalid_labels = sum(record.get("label") not in (0, 1) for record in records)
    event_ids = [record["event_id"] for record in records]
    segment_counts = {segment: 0 for segment in SEGMENTS}
    for record in records:
        if record["segment"] in segment_counts:
            segment_counts[record["segment"]] += 1
    label_rate = sum(record["label"] for record in records) / len(records)
    schema = {name: type(records[0][name]).__name__ for name in sorted(required)}
    schema_fingerprint = hashlib.sha256(
        json.dumps({"schema": schema, "features": FEATURE_NAMES}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    checks = {
        "record_count_matches": len(records) == expected_records,
        "all_required_fields_present": not missing_fields,
        "feature_vector_width_valid": invalid_features == 0,
        "binary_labels_valid": invalid_labels == 0,
        "unique_event_ids": len(set(event_ids)) == len(event_ids),
        "all_expected_segments_present": all(segment_counts.values()),
        "label_rate_in_reasonable_range": 0.05 < label_rate < 0.95,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "record_count": len(records),
        "expected_record_count": expected_records,
        "label_rate": label_rate,
        "segment_counts": segment_counts,
        "schema": schema,
        "schema_fingerprint": schema_fingerprint,
        "checks": checks,
        "missing_fields": missing_fields,
        "invalid_feature_records": invalid_features,
        "invalid_label_records": invalid_labels,
    }


@ray.remote
def fit_candidate(
    name: str,
    records: list[dict[str, Any]],
    learning_rate: float,
    epochs: int,
    l2: float,
) -> dict[str, Any]:
    """Train one logistic ranker; four invocations form the trial sweep."""
    weights = [0.0] * len(FEATURE_NAMES)
    loss_history: list[float] = []
    for _ in range(epochs):
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
        count = len(records)
        weights = [
            weight - learning_rate * ((value / count) + l2 * weight)
            for weight, value in zip(weights, gradient)
        ]
        loss_history.append(loss / count)
    return {
        "name": name,
        "weights": weights,
        "learning_rate": learning_rate,
        "epochs": epochs,
        "l2": l2,
        "final_training_loss": loss_history[-1],
        "loss_history": loss_history,
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
    for user_records in grouped.values():
        ordered = [label for _, label in sorted(user_records, reverse=True)[:k]]
        ideal = sorted((label for _, label in user_records), reverse=True)[:k]
        dcg = sum(label / math.log2(index + 2) for index, label in enumerate(ordered))
        ideal_dcg = sum(label / math.log2(index + 2) for index, label in enumerate(ideal))
        values.append(dcg / ideal_dcg if ideal_dcg else 1.0)
    return sum(values) / len(values)


def evaluate(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = predicted_scores(model, records)
    metric_values = {
        "auc": auc(records, scores),
        "ndcg_at_10": ndcg_at_k(records, scores),
        "log_loss": sum(
            -record["label"] * math.log(score + 1e-12)
            - (1 - record["label"]) * math.log(1 - score + 1e-12)
            for record, score in zip(records, scores)
        )
        / len(records),
    }
    segments: dict[str, dict[str, float]] = {}
    for segment in SEGMENTS:
        segment_records = [record for record in records if record["segment"] == segment]
        segment_scores = [score for record, score in zip(records, scores) if record["segment"] == segment]
        segments[segment] = {
            "records": len(segment_records),
            "auc": auc(segment_records, segment_scores),
            "ndcg_at_10": ndcg_at_k(segment_records, segment_scores),
        }
    metric_values["segments"] = segments
    return metric_values


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


def read_json(client: Any, bucket: str, key: str) -> Any:
    return json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))


def top_recommendations(model: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    feature_weights = dict(zip(FEATURE_NAMES, model["weights"]))
    for record in records:
        score = sigmoid(dot(model["weights"], record["features"]))
        contributing_feature = max(
            FEATURE_NAMES[1:],
            key=lambda name: feature_weights[name] * record["features"][FEATURE_NAMES.index(name)],
        )
        by_user[record["user_id"]].append(
            {
                "user_id": record["user_id"],
                "item_id": record["item_id"],
                "segment": record["segment"],
                "ranking_score": score,
                "top_contributing_feature": contributing_feature,
            }
        )
    recommendations: list[dict[str, Any]] = []
    for user, scored in sorted(by_user.items()):
        for rank, item in enumerate(sorted(scored, key=lambda value: value["ranking_score"], reverse=True)[:5], 1):
            recommendations.append({"rank": rank, **item})
    return recommendations


def run() -> None:
    run_id = (os.environ.get("MLOPS_RUN_ID", "ranking-advanced-20260902") + "-" + uuid.uuid4().hex[:8])
    users = int(os.environ.get("RANKING_USERS", "120"))
    items_per_user = int(os.environ.get("RANKING_ITEMS_PER_USER", "24"))
    seed = int(os.environ.get("RANKING_SEED", "20260902"))
    ray.init(address=os.environ.get("RAY_ADDRESS", "auto"), runtime_env=json.loads(os.environ.get("OCI_DEMO_RAY_RUNTIME_ENV", "{}")))
    client, bucket, endpoint = storage_client()
    prefix = f"mlops-demo/personalization-ranking/{run_id}"

    records = make_events(users, items_per_user, seed)
    quality = quality_report(records, users * items_per_user)
    raw_uri = put_json(client, bucket, f"{prefix}/data/raw-events.json", records)
    quality_uri = put_json(client, bucket, f"{prefix}/quality/data-quality-report.json", quality)
    feature_contract = {
        "feature_names": list(FEATURE_NAMES),
        "feature_count": len(FEATURE_NAMES),
        "label": "clicked",
        "entity_keys": ["user_id", "item_id"],
        "segment_key": "segment",
        "schema_fingerprint": quality["schema_fingerprint"],
    }
    contract_uri = put_json(client, bucket, f"{prefix}/features/feature-contract.json", feature_contract)
    if quality["status"] != "passed":
        raise RuntimeError("data-quality gate failed; see data-quality-report artifact")

    train_records = [record for record in records if int(record["user_id"].split("-")[1]) % 5 != 0]
    validation_records = [record for record in records if int(record["user_id"].split("-")[1]) % 5 == 0]
    trial_parameters = [
        ("baseline-underfit", 0.025, 3, 0.00),
        ("ranker-balanced", 0.10, 12, 0.005),
        ("ranker-regularized", 0.16, 18, 0.020),
        ("ranker-fast", 0.24, 14, 0.002),
    ]
    candidates = ray.get(
        [fit_candidate.remote(name, train_records, learning_rate, epochs, l2) for name, learning_rate, epochs, l2 in trial_parameters]
    )
    for candidate in candidates:
        candidate["metrics"] = evaluate(candidate, validation_records)
    baseline = next(candidate for candidate in candidates if candidate["name"] == "baseline-underfit")
    # Prefer ranking quality, then calibrated probabilities, then AUC. This
    # avoids selecting an arbitrary first candidate when NDCG is tied.
    champion = max(
        candidates,
        key=lambda candidate: (
            candidate["metrics"]["ndcg_at_10"],
            -candidate["metrics"]["log_loss"],
            candidate["metrics"]["auc"],
        ),
    )
    worst_segment_ndcg = min(
        values["ndcg_at_10"] for values in champion["metrics"]["segments"].values()
    )
    gate = {
        "minimum_auc": 0.83,
        "minimum_ndcg_at_10": 0.80,
        "minimum_segment_ndcg_at_10": 0.76,
        "minimum_ranking_improvement_over_baseline": 0.001,
        "minimum_log_loss_reduction": 0.05,
        "actual_auc": champion["metrics"]["auc"],
        "actual_ndcg_at_10": champion["metrics"]["ndcg_at_10"],
        "actual_worst_segment_ndcg_at_10": worst_segment_ndcg,
        "actual_ranking_improvement_over_baseline": champion["metrics"]["ndcg_at_10"] - baseline["metrics"]["ndcg_at_10"],
        "actual_log_loss_reduction": baseline["metrics"]["log_loss"] - champion["metrics"]["log_loss"],
    }
    gate["passed"] = (
        gate["actual_auc"] >= gate["minimum_auc"]
        and gate["actual_ndcg_at_10"] >= gate["minimum_ndcg_at_10"]
        and gate["actual_worst_segment_ndcg_at_10"] >= gate["minimum_segment_ndcg_at_10"]
        and gate["actual_ranking_improvement_over_baseline"]
        >= gate["minimum_ranking_improvement_over_baseline"]
        and gate["actual_log_loss_reduction"] >= gate["minimum_log_loss_reduction"]
    )
    leaderboard_uri = put_json(
        client,
        bucket,
        f"{prefix}/experiments/leaderboard.json",
        {"run_id": run_id, "candidates": candidates, "promotion_gate": gate},
    )
    if not gate["passed"]:
        raise RuntimeError("promotion gate failed; no model artifact was promoted")

    model_uri = put_json(client, bucket, f"{prefix}/models/champion.json", champion)
    model_key = f"{prefix}/models/champion.json"
    reloaded_model = read_json(client, bucket, model_key)
    reload_validation = {
        "artifact_uri": model_uri,
        "model_name_matches": reloaded_model["name"] == champion["name"],
        "weights_match": reloaded_model["weights"] == champion["weights"],
        "status": "passed" if reloaded_model == champion else "failed",
    }
    reload_uri = put_json(client, bucket, f"{prefix}/validation/model-reload.json", reload_validation)
    if reload_validation["status"] != "passed":
        raise RuntimeError("model artifact reload validation failed")

    scoring_records = make_events(30, 20, seed + 1)
    recommendations = top_recommendations(champion, scoring_records)
    batch_uri = put_json(client, bucket, f"{prefix}/scores/explainable-batch-recommendations.json", recommendations)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "execution_engine": "KubeRay on OCI OKE",
        "data_quality_status": quality["status"],
        "feature_contract": feature_contract,
        "training_records": len(train_records),
        "validation_records": len(validation_records),
        "candidate_count": len(candidates),
        "champion": champion["name"],
        "metrics": champion["metrics"],
        "promotion_gate": gate,
        "artifacts": {
            "raw_data": raw_uri,
            "quality_report": quality_uri,
            "feature_contract": contract_uri,
            "leaderboard": leaderboard_uri,
            "model": model_uri,
            "model_reload_validation": reload_uri,
            "batch_scores": batch_uri,
        },
    }
    manifest_uri = put_json(client, bucket, f"{prefix}/run-manifest.json", manifest)
    result = {
        "status": "promoted",
        "run_id": run_id,
        "champion": champion["name"],
        "metrics": champion["metrics"],
        "promotion_gate": gate,
        "artifacts": {**manifest["artifacts"], "manifest": manifest_uri},
    }
    print("MLOPS_RESULT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    run()
