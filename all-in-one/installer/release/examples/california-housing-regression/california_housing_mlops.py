"""California Housing-style, CPU-only regression MLOps demo for OCI.

This mirrors the lifecycle of Michelangelo AI's California Housing XGBoost
example—quality checks, feature preparation, distributed training, evaluation,
registry-ready artifact, and batch prediction—without adding Spark or XGBoost
to the intentionally small playground image. All housing data is synthetic.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
import random
from datetime import datetime, timezone
from typing import Any

import boto3
import ray

FEATURES = ("bias", "median_income", "rooms_per_household", "population_density", "latitude", "coastal_access")


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def make_records(count: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    records = []
    for index in range(count):
        income, rooms, density, latitude, coastal = [rng.random() for _ in range(5)]
        value = 1.35 + 2.65 * income + 0.62 * rooms - 1.05 * density + 0.38 * latitude + 0.84 * coastal + rng.uniform(-0.13, 0.13)
        records.append({"tract_id": f"tract-{index:05d}", "county": ("Alameda", "Orange", "San Diego", "Santa Clara")[index % 4], "features": [1.0, income, rooms, density, latitude, coastal], "median_house_value_index": value})
    return records


def quality(records: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    required = {"tract_id", "county", "features", "median_house_value_index"}
    counties = {record["county"] for record in records}
    targets = [record["median_house_value_index"] for record in records]
    checks = {
        "record_count_matches": len(records) == expected,
        "tract_ids_unique": len({record["tract_id"] for record in records}) == len(records),
        "feature_width_valid": all(len(record["features"]) == len(FEATURES) for record in records),
        "targets_numeric": all(isinstance(value, float) for value in targets),
        "all_counties_present": len(counties) == 4,
        "all_required_fields_present": all(required <= set(record) for record in records),
    }
    fingerprint = hashlib.sha256(json.dumps({"features": FEATURES, "fields": sorted(required)}, sort_keys=True).encode()).hexdigest()
    return {"status": "passed" if all(checks.values()) else "failed", "record_count": len(records), "counties": sorted(counties), "target_min": min(targets), "target_max": max(targets), "schema_fingerprint": fingerprint, "checks": checks}


@ray.remote
def fit_regressor(name: str, records: list[dict[str, Any]], learning_rate: float, epochs: int, l2: float) -> dict[str, Any]:
    weights = [0.0] * len(FEATURES)
    loss_history = []
    for _ in range(epochs):
        gradient = [0.0] * len(weights)
        squared_error = 0.0
        for record in records:
            error = dot(weights, record["features"]) - record["median_house_value_index"]
            squared_error += error * error
            for index, value in enumerate(record["features"]):
                gradient[index] += error * value
        weights = [weight - learning_rate * ((value / len(records)) + l2 * weight) for weight, value in zip(weights, gradient)]
        loss_history.append(squared_error / len(records))
    return {"name": name, "weights": weights, "learning_rate": learning_rate, "epochs": epochs, "l2": l2, "loss_history": loss_history, "final_training_mse": loss_history[-1]}


def evaluate(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    actual = [record["median_house_value_index"] for record in records]
    predicted = [dot(model["weights"], record["features"]) for record in records]
    errors = [prediction - target for prediction, target in zip(predicted, actual)]
    mse = sum(error * error for error in errors) / len(errors)
    mean = sum(actual) / len(actual)
    total_variance = sum((target - mean) ** 2 for target in actual)
    county_metrics = {}
    for county in sorted({record["county"] for record in records}):
        county_errors = [error for record, error in zip(records, errors) if record["county"] == county]
        county_metrics[county] = {"records": len(county_errors), "rmse": math.sqrt(sum(error * error for error in county_errors) / len(county_errors)), "mae": sum(abs(error) for error in county_errors) / len(county_errors)}
    return {"rmse": math.sqrt(mse), "mae": sum(abs(error) for error in errors) / len(errors), "r2": 1 - sum(error * error for error in errors) / total_variance, "county_metrics": county_metrics}


def storage_client() -> tuple[Any, str]:
    endpoint = os.environ["OCI_S3_ENDPOINT"]
    endpoint = endpoint if endpoint.startswith("http") else f"https://{endpoint}"
    return boto3.client("s3", endpoint_url=endpoint, region_name=os.environ["AWS_REGION"]), os.environ["OCI_S3_BUCKET"]


def put(client: Any, bucket: str, key: str, value: Any) -> str:
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(value, indent=2, sort_keys=True).encode(), ContentType="application/json")
    return f"s3://{bucket}/{key}"


def run() -> None:
    run_id, seed = (os.environ.get("MLOPS_RUN_ID", "california-housing-20260902") + "-" + uuid.uuid4().hex[:8]), 20260902
    ray.init(address=os.environ.get("RAY_ADDRESS", "auto"), runtime_env=json.loads(os.environ.get("OCI_DEMO_RAY_RUNTIME_ENV", "{}")))
    client, bucket = storage_client()
    prefix = f"mlops-demo/california-housing/{run_id}"
    records = make_records(1200, seed)
    report = quality(records, 1200)
    raw_uri, quality_uri = put(client, bucket, f"{prefix}/data/synthetic-housing-records.json", records), put(client, bucket, f"{prefix}/quality/data-quality-report.json", report)
    contract = {"features": list(FEATURES), "target": "median_house_value_index", "entity_key": "tract_id", "schema_fingerprint": report["schema_fingerprint"]}
    contract_uri = put(client, bucket, f"{prefix}/features/feature-contract.json", contract)
    if report["status"] != "passed": raise RuntimeError("housing data quality gate failed")
    train, validation = records[:960], records[960:]
    configurations = [("linear-baseline", 0.01, 1, 0.0), ("linear-balanced", 0.16, 300, 0.002), ("ridge-robust", 0.18, 450, 0.005)]
    candidates = ray.get([fit_regressor.remote(name, train, learning_rate, epochs, l2) for name, learning_rate, epochs, l2 in configurations])
    for candidate in candidates: candidate["metrics"] = evaluate(candidate, validation)
    baseline = candidates[0]
    champion = min(candidates, key=lambda candidate: candidate["metrics"]["rmse"])
    worst_county_rmse = max(value["rmse"] for value in champion["metrics"]["county_metrics"].values())
    gate = {"maximum_rmse": 0.18, "minimum_r2": 0.96, "maximum_county_rmse": 0.20, "minimum_rmse_reduction": 0.25, "actual_rmse": champion["metrics"]["rmse"], "actual_r2": champion["metrics"]["r2"], "actual_worst_county_rmse": worst_county_rmse, "actual_rmse_reduction": baseline["metrics"]["rmse"] - champion["metrics"]["rmse"]}
    gate["passed"] = all((gate["actual_rmse"] <= gate["maximum_rmse"], gate["actual_r2"] >= gate["minimum_r2"], gate["actual_worst_county_rmse"] <= gate["maximum_county_rmse"], gate["actual_rmse_reduction"] >= gate["minimum_rmse_reduction"]))
    leaderboard_uri = put(client, bucket, f"{prefix}/experiments/leaderboard.json", {"run_id": run_id, "candidates": candidates, "promotion_gate": gate})
    if not gate["passed"]: raise RuntimeError("housing promotion gate failed")
    model_uri = put(client, bucket, f"{prefix}/models/champion.json", champion)
    reloaded = json.loads(client.get_object(Bucket=bucket, Key=f"{prefix}/models/champion.json")["Body"].read().decode())
    reload_uri = put(client, bucket, f"{prefix}/validation/model-reload.json", {"artifact_uri": model_uri, "status": "passed" if reloaded == champion else "failed", "weights_match": reloaded == champion})
    if reloaded != champion: raise RuntimeError("model reload validation failed")
    scoring = make_records(24, seed + 10)
    predictions = [{"tract_id": record["tract_id"], "county": record["county"], "predicted_median_house_value_index": dot(champion["weights"], record["features"]), "top_feature_driver": max(FEATURES[1:], key=lambda feature: abs(champion["weights"][FEATURES.index(feature)] * record["features"][FEATURES.index(feature)]))} for record in scoring]
    batch_uri = put(client, bucket, f"{prefix}/scores/batch-housing-predictions.json", predictions)
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "execution_engine": "KubeRay on OCI OKE", "training_records": len(train), "validation_records": len(validation), "candidate_count": len(candidates), "champion": champion["name"], "metrics": champion["metrics"], "promotion_gate": gate, "artifacts": {"raw_data": raw_uri, "quality_report": quality_uri, "feature_contract": contract_uri, "leaderboard": leaderboard_uri, "model": model_uri, "model_reload_validation": reload_uri, "batch_scores": batch_uri}}
    manifest_uri = put(client, bucket, f"{prefix}/run-manifest.json", manifest)
    print("MLOPS_RESULT=" + json.dumps({"status": "promoted", "run_id": run_id, "champion": champion["name"], "metrics": champion["metrics"], "promotion_gate": gate, "artifacts": {**manifest["artifacts"], "manifest": manifest_uri}}, sort_keys=True))


if __name__ == "__main__": run()
