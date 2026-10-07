"""Artifact-backed, CPU-only fraud anomaly-detection demo for OCI OKE.

Synthetic transactions are used solely to demonstrate the MLOps operating
pattern: contract checks, Ray-distributed candidate fitting, segment gates,
investigation evidence, Object Storage persistence, and Studio registration.
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

FEATURES = ("amount_risk", "velocity_risk", "device_risk", "geo_risk", "account_risk")
CHANNELS = ("card_present", "ecommerce", "wallet")


def median(values: list[float]) -> float:
    ordered = sorted(values); halfway = len(ordered) // 2
    return ordered[halfway] if len(ordered) % 2 else (ordered[halfway - 1] + ordered[halfway]) / 2


def make_transactions(count: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed); transactions = []
    for index in range(count):
        # 17 and the three channel positions are coprime, so the synthetic
        # positives are distributed across every channel for the segment gate.
        fraud = index % 17 == 0
        channel = CHANNELS[index % len(CHANNELS)]
        base = [min(1.0, max(0.0, rng.gauss(0.32, 0.13))) for _ in FEATURES]
        if fraud:
            base = [min(1.0, max(0.0, value + rng.uniform(0.30, 0.62))) for value in base]
            if channel == "ecommerce": base[2] = min(1.0, base[2] + 0.12)
        transactions.append({"transaction_id": f"txn-{index:06d}", "account_id": f"account-{index % 240:03d}", "channel": channel, "region": ("NA", "EMEA", "APAC")[index % 3], "features": base, "confirmed_fraud": int(fraud)})
    return transactions


def quality(records: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    required = {"transaction_id", "account_id", "channel", "region", "features", "confirmed_fraud"}
    fraud_rate = sum(record["confirmed_fraud"] for record in records) / len(records)
    checks = {"record_count_matches": len(records) == expected, "transaction_ids_unique": len({record["transaction_id"] for record in records}) == len(records), "feature_width_valid": all(len(record["features"]) == len(FEATURES) for record in records), "binary_labels_valid": all(record["confirmed_fraud"] in (0, 1) for record in records), "all_channels_present": {record["channel"] for record in records} == set(CHANNELS), "fraud_rate_reasonable": 0.03 < fraud_rate < 0.25, "fields_present": all(required <= set(record) for record in records)}
    fingerprint = hashlib.sha256(json.dumps({"features": FEATURES, "fields": sorted(required)}, sort_keys=True).encode()).hexdigest()
    return {"status": "passed" if all(checks.values()) else "failed", "record_count": len(records), "fraud_rate": fraud_rate, "schema_fingerprint": fingerprint, "checks": checks}


@ray.remote
def fit_candidate(name: str, records: list[dict[str, Any]], threshold: float, weights: list[float]) -> dict[str, Any]:
    centers = [median([record["features"][index] for record in records]) for index in range(len(FEATURES))]
    mads = [max(0.05, median([abs(record["features"][index] - centers[index]) for record in records])) for index in range(len(FEATURES))]
    return {"name": name, "threshold": threshold, "weights": weights, "centers": centers, "mads": mads}


def anomaly_score(model: dict[str, Any], record: dict[str, Any]) -> float:
    return sum(weight * abs(value - center) / mad for value, center, mad, weight in zip(record["features"], model["centers"], model["mads"], model["weights"]))


def auc(labels: list[int], scores: list[float]) -> float:
    positives, negatives = sum(labels), len(labels) - sum(labels)
    if not positives or not negatives: return 0.5
    ranked = sorted(zip(scores, labels), key=lambda pair: pair[0])
    rank_sum = sum(index + 1 for index, (_, label) in enumerate(ranked) if label)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def metrics(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [anomaly_score(model, record) for record in records]
    predictions = [int(score >= model["threshold"]) for score in scores]
    labels = [record["confirmed_fraud"] for record in records]
    def values(indices: list[int]) -> dict[str, float]:
        tp = sum(predictions[i] and labels[i] for i in indices); fp = sum(predictions[i] and not labels[i] for i in indices); fn = sum(not predictions[i] and labels[i] for i in indices)
        precision = tp / (tp + fp) if tp + fp else 0.0; recall = tp / (tp + fn) if tp + fn else 0.0
        return {"precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0, "flag_rate": sum(predictions[i] for i in indices) / len(indices)}
    all_indices = list(range(len(records))); result = values(all_indices)
    result["auc"] = auc(labels, scores)
    result["channels"] = {channel: {"records": len(indices), **values(indices)} for channel in CHANNELS for indices in [[i for i, record in enumerate(records) if record["channel"] == channel]]}
    return result


def storage() -> tuple[Any, str]:
    endpoint = os.environ["OCI_S3_ENDPOINT"]; endpoint = endpoint if endpoint.startswith("http") else f"https://{endpoint}"
    return boto3.client("s3", endpoint_url=endpoint, region_name=os.environ["AWS_REGION"]), os.environ["OCI_S3_BUCKET"]


def put(client: Any, bucket: str, key: str, value: Any) -> str:
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(value, indent=2, sort_keys=True).encode(), ContentType="application/json")
    return f"s3://{bucket}/{key}"


def run() -> None:
    run_id, seed = (os.environ.get("MLOPS_RUN_ID", "fraud-anomaly-20260903") + "-" + uuid.uuid4().hex[:8]), 20260903
    ray.init(address=os.environ.get("RAY_ADDRESS", "auto"), runtime_env=json.loads(os.environ.get("OCI_DEMO_RAY_RUNTIME_ENV", "{}"))); client, bucket = storage(); prefix = f"mlops-demo/fraud-anomaly-detection/{run_id}"
    records = make_transactions(1800, seed); report = quality(records, 1800)
    raw_uri, quality_uri = put(client, bucket, f"{prefix}/data/synthetic-transactions.json", records), put(client, bucket, f"{prefix}/quality/data-quality-report.json", report)
    contract = {"entity_key": "transaction_id", "features": list(FEATURES), "label": "confirmed_fraud", "segments": ["channel"], "schema_fingerprint": report["schema_fingerprint"]}
    contract_uri = put(client, bucket, f"{prefix}/features/feature-contract.json", contract)
    if report["status"] != "passed": raise RuntimeError("transaction data quality gate failed")
    train, validation = records[:1440], records[1440:]
    # Thresholds reflect the robust score separation observed on the training
    # distribution; the sensitivity candidate deliberately admits near-boundary
    # records, while the balanced candidate controls review-queue volume.
    settings = [("balanced", 12.5, [1.0, 1.0, 1.0, 1.0, 1.0]), ("conservative", 14.0, [1.0, 1.0, 1.0, 1.0, 1.0]), ("sensitive", 10.7, [1.0, 1.0, 1.0, 1.0, 1.0])]
    candidates = ray.get([fit_candidate.remote(name, train, threshold, weights) for name, threshold, weights in settings])
    for candidate in candidates: candidate["metrics"] = metrics(candidate, validation)
    champion = max(candidates, key=lambda candidate: (candidate["metrics"]["f1"], candidate["metrics"]["recall"], candidate["metrics"]["precision"]))
    lowest_segment_recall = min(value["recall"] for value in champion["metrics"]["channels"].values())
    gate = {"minimum_precision": 0.78, "minimum_recall": 0.78, "minimum_f1": 0.78, "minimum_auc": 0.90, "minimum_channel_recall": 0.70, "actual_precision": champion["metrics"]["precision"], "actual_recall": champion["metrics"]["recall"], "actual_f1": champion["metrics"]["f1"], "actual_auc": champion["metrics"]["auc"], "actual_lowest_channel_recall": lowest_segment_recall}
    gate["passed"] = all((gate["actual_precision"] >= gate["minimum_precision"], gate["actual_recall"] >= gate["minimum_recall"], gate["actual_f1"] >= gate["minimum_f1"], gate["actual_auc"] >= gate["minimum_auc"], gate["actual_lowest_channel_recall"] >= gate["minimum_channel_recall"]))
    leaderboard_uri = put(client, bucket, f"{prefix}/experiments/candidate-leaderboard.json", {"run_id": run_id, "candidates": candidates, "promotion_gate": gate})
    if not gate["passed"]: raise RuntimeError("fraud anomaly promotion gate failed")
    model_uri = put(client, bucket, f"{prefix}/models/champion.json", champion)
    reloaded = json.loads(client.get_object(Bucket=bucket, Key=f"{prefix}/models/champion.json")["Body"].read().decode())
    reload_uri = put(client, bucket, f"{prefix}/validation/model-reload.json", {"artifact_uri": model_uri, "status": "passed" if reloaded == champion else "failed", "parameters_match": reloaded == champion})
    if reloaded != champion: raise RuntimeError("model reload validation failed")
    queue = [{"transaction_id": record["transaction_id"], "account_id": record["account_id"], "channel": record["channel"], "region": record["region"], "anomaly_score": anomaly_score(champion, record), "risk_band": "high" if anomaly_score(champion, record) > champion["threshold"] * 1.25 else "review", "top_risk_driver": FEATURES[max(range(len(FEATURES)), key=lambda index: champion["weights"][index] * abs(record["features"][index] - champion["centers"][index]) / champion["mads"][index])]} for record in validation if anomaly_score(champion, record) >= champion["threshold"]]
    queue.sort(key=lambda record: record["anomaly_score"], reverse=True)
    queue_uri = put(client, bucket, f"{prefix}/scores/investigation-queue.json", queue)
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "execution_engine": "KubeRay on OCI OKE", "training_records": len(train), "validation_records": len(validation), "candidate_count": len(candidates), "champion": champion["name"], "metrics": champion["metrics"], "promotion_gate": gate, "artifacts": {"raw_data": raw_uri, "quality_report": quality_uri, "feature_contract": contract_uri, "leaderboard": leaderboard_uri, "model": model_uri, "model_reload_validation": reload_uri, "investigation_queue": queue_uri}}
    manifest_uri = put(client, bucket, f"{prefix}/run-manifest.json", manifest)
    print("MLOPS_RESULT=" + json.dumps({"status": "promoted", "run_id": run_id, "champion": champion["name"], "metrics": champion["metrics"], "promotion_gate": gate, "artifacts": {**manifest["artifacts"], "manifest": manifest_uri}}, sort_keys=True))


if __name__ == "__main__": run()
