#!/usr/bin/env python3
"""Install the persistent OCI IAM application membership gateway.

The installer supplies a digest-pinned image and a DB-only scoped credential.
No JWT, database password or rendered Kubernetes Secret is logged.
"""
import hashlib
import json
import subprocess
from urllib.parse import urlsplit

import auth

NAME = "ma-identity"


def resources(config, namespace="ma-system", metadata=None):
    metadata = metadata or auth.discover(config)
    image = config["identity_image"]
    if "@sha256:" not in image:
        raise ValueError("identity_image must be pinned to its pushed SHA256 digest")
    emails = config["authorized_emails"]
    admins = config.get("bootstrap_admin_emails") or emails[:1]
    if not admins or any(not auth.EMAIL.fullmatch(e) for e in admins):
        raise ValueError("At least one exact bootstrap administrator email is required")
    origin = urlsplit(config["oidc_issuer"])
    settings = {
        "domain_id": "https://" + origin.hostname.lower(),
        "issuer": metadata["issuer"], "jwks_uri": metadata["jwks_uri"],
        "userinfo_endpoint": metadata["userinfo_endpoint"],
        "audience": config["oidc_client_id"],
        "mysql_hostname": config["mysql_expected_hostname"],
        "mysql_port": int(config.get("mysql_port", 3306)),
        "mysql_database": config["identity_mysql_database"],
        "mysql_username": config["identity_mysql_username"],
        "mysql_password": config["identity_mysql_password"],
        "mysql_ca_file": "/etc/identity/mysql-ca.pem",
        "public_origin": "https://" + config["public_hostname"],
        "upstream_url": "http://michelangelo-ui." + namespace + ".svc.cluster.local:80/",
        "bootstrap_admin_emails": admins, "initial_operator_emails": emails,
    }
    secret_data = {"config.json": json.dumps(settings, sort_keys=True),
                   "mysql-ca.pem": config["mysql_ca_pem"]}
    labels = {"app.kubernetes.io/name": NAME}
    def obj(kind, api="v1", **fields):
        return {"apiVersion": api, "kind": kind,
                "metadata": {"name": NAME, "namespace": namespace, "labels": labels}, **fields}
    secret = obj("Secret", type="Opaque", stringData=secret_data)
    ports = [{"name": "browser", "containerPort": 8080}, {"name": "resolve", "containerPort": 8081}]
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    deployment = obj("Deployment", api="apps/v1", spec={"replicas": 1,
        "selector": {"matchLabels": labels},
        "template": {"metadata": {"labels": labels, "annotations": {
            "checksum/config": hashlib.sha256(json.dumps(secret_data, sort_keys=True).encode()).hexdigest()}},
        "spec": {"automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": config.get("identity_image_pull_secret", "michelangelo-registry")}],
            "hostAliases": [{"ip": config["mysql_host"], "hostnames": [config["mysql_expected_hostname"]]}],
            "nodeSelector": config.get("identity_node_selector", {"workload.oci.oracle.com/pool": "system"}),
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                "fsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": NAME, "image": image, "ports": ports,
                "securityContext": security,
                "volumeMounts": [{"name": "config", "mountPath": "/etc/identity", "readOnly": True}],
                "startupProbe": {"httpGet": {"path": "/ready", "port": 8081}, "failureThreshold": 30, "periodSeconds": 5},
                "readinessProbe": {"httpGet": {"path": "/ready", "port": 8081}, "periodSeconds": 5},
                "livenessProbe": {"httpGet": {"path": "/healthz", "port": 8081}, "periodSeconds": 15},
                "resources": {"requests": {"cpu": "50m", "memory": "96Mi"}, "limits": {"memory": "256Mi"}}}],
            "volumes": [{"name": "config", "secret": {"secretName": NAME, "defaultMode": 288}}]}}})
    service = obj("Service", spec={"type": "ClusterIP", "selector": labels, "ports": [
        {"name": "browser", "port": 8080, "targetPort": 8080},
        {"name": "resolve", "port": 8081, "targetPort": 8081}]})
    return [secret, deployment, service]


def install(config, kubectl=None, namespace="ma-system"):
    command = list(kubectl or ["kubectl"])
    metadata = auth.discover(config)
    auth.verify_signing_keys(metadata)
    payload = {"apiVersion": "v1", "kind": "List", "items": resources(config, namespace, metadata)}
    result = subprocess.run(command + ["apply", "-f", "-"], input=json.dumps(payload), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Application identity apply failed; inspect events without printing Secrets")
    subprocess.run(command + ["-n", namespace, "rollout", "status", "deployment/" + NAME, "--timeout=10m"], check=True)
