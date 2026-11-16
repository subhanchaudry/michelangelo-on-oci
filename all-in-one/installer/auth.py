#!/usr/bin/env python3
"""OCI IAM browser gateway: Caddy HTTPS -> OAuth2 Proxy -> private Studio.

No tokens, passwords, or rendered Secrets are printed. The public Service uses
TCP so Caddy receives HTTPS itself and retains its ACME certificates on a PVC.
The app, API, logs and workflow engines remain private ClusterIP services.
"""
import base64
import hashlib
import ipaddress
import json
import re
import secrets
import subprocess
from urllib.error import HTTPError
from urllib.parse import urlsplit, urljoin, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen

NAME = "ma-browser-edge"
DEFAULT_CADDY = "docker.io/library/caddy:2.11.6-alpine@sha256:d44355d3c2149dc580ce2cac735955d1c08d3d00882c30489c241aa51a5c10d9"
DEFAULT_PROXY = "quay.io/oauth2-proxy/oauth2-proxy:v7.15.6@sha256:01da265f8e371e6d3043b97829e171f3e4d53f9dc3817be15fb4fc29c92fe2a6"
DEFAULT_SESSION_STORE = "docker.io/library/redis:8.2-alpine@sha256:a18fce2f705d9782ccf6968f8dd94fa04566ff97e10a8d322ad72645243e4c0c"
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


def validate(config):
    host = config.get("public_hostname", "")
    if not HOST.fullmatch(host) or "." not in host:
        raise ValueError("public_hostname must be a DNS hostname without a port or path")
    issuer = urlsplit(config.get("oidc_issuer", ""))
    if issuer.scheme != "https" or not issuer.hostname or issuer.username or issuer.password or issuer.query or issuer.fragment or issuer.path not in ("", "/") or issuer.port not in (None, 443):
        raise ValueError("oidc_issuer must be an HTTPS identity-domain origin")
    for key in ("oidc_client_id", "oidc_client_secret"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(key + " is required")
    emails = config.get("authorized_emails")
    if not isinstance(emails, list) or not emails or any(not isinstance(e, str) or not EMAIL.fullmatch(e) for e in emails):
        raise ValueError("authorized_emails requires exact email addresses without wildcards")
    if not EMAIL.fullmatch(config.get("acme_email", "")):
        raise ValueError("acme_email must be an email address")
    try:
        ipaddress.IPv4Address(config["reserved_public_ip"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("reserved_public_ip must contain the reserved IPv4 address, not its OCID") from None
    subnets = config.get("lb_subnet_ids", [config.get("lb_subnet_id", "")])
    if not isinstance(subnets, list) or not 1 <= len(subnets) <= 2 or any(not s.startswith("ocid1.subnet.") for s in subnets):
        raise ValueError("One regional or two AD-specific lb_subnet_ids are required")
    return subnets


def discover(config):
    """Use the actual domain's advertised issuer/endpoints, with origin checks.

Oracle domains can include :443 in their administrative URL. Metadata issuer
can omit it; use the provider's issuer exactly, retaining all JWT checks.
"""
    configured = config["oidc_issuer"].rstrip("/")
    with urlopen(configured + "/.well-known/openid-configuration", timeout=30) as response:
        metadata = json.load(response)
    return validate_metadata(config, metadata)


def validate_metadata(config, metadata):
    """Allow Oracle's fixed global issuer with tenant-specific signing keys.

The observed default domain advertises https://identity.oraclecloud.com/.
All OAuth, UserInfo, and JWKS network requests stay on the selected domain;
issuer checking remains enabled and uses this exact advertised value.
"""
    expected = urlsplit(config["oidc_issuer"])
    issuer = metadata.get("issuer", "")
    parsed_issuer = urlsplit(issuer)
    same_domain = (parsed_issuer.scheme == "https" and parsed_issuer.hostname == expected.hostname
                   and parsed_issuer.port in (None, 443) and parsed_issuer.path in ("", "/")
                   and not parsed_issuer.username and not parsed_issuer.password
                   and not parsed_issuer.query and not parsed_issuer.fragment)
    if issuer != "https://identity.oraclecloud.com/" and not same_domain:
        raise ValueError("Identity-domain discovery returned an unexpected issuer")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint"):
        parsed = urlsplit(metadata.get(key, ""))
        if parsed.scheme != "https" or parsed.hostname != expected.hostname or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Identity-domain discovery returned an unexpected " + key)
    return metadata


def verify_signing_keys(metadata):
    """Require usable public verification keys before accepting OIDC startup."""
    try:
        with urlopen(metadata['jwks_uri'], timeout=30) as response:
            keys = json.load(response).get('keys', [])
    except HTTPError as error:
        if error.code in (401, 403):
            raise RuntimeError('Identity-domain signing certificates are not public: enable Access signing certificate / Configure client access (Settings.signingCertPublicAccess) before deploying OIDC') from None
        raise
    if not keys or not any(key.get('kty') == 'RSA' and key.get('n') and key.get('e') for key in keys):
        raise RuntimeError('Identity domain did not return usable public RSA verification keys')
    if any(any(field in key for field in ('d', 'p', 'q', 'dp', 'dq', 'qi')) for key in keys):
        raise RuntimeError('Identity-domain JWKS unexpectedly contains private key material')
    return len(keys)


def oauth_config(config, ui_service="ma-identity", namespace="ma-system", ui_port=8080, metadata=None):
    settings = {
        "provider": "oidc", "provider_display_name": "OCI IAM",
        "http_address": "127.0.0.1:4180",
        "redirect_url": "https://" + config["public_hostname"] + "/oauth2/callback",
        "oidc_issuer_url": config["oidc_issuer"].rstrip("/"),
        "scope": "openid email profile", "code_challenge_method": "S256",
        "insecure_oidc_skip_nonce": False,
        "insecure_oidc_skip_issuer_verification": False,
        "insecure_oidc_allow_unverified_email": False,
        # Selected-domain authentication is followed by the persistent directory
        # on every application request. Invitations/removals need no rollout.
        "email_domains": ["*"],
        "upstreams": [f"http://{ui_service}.{namespace}.svc.cluster.local:{ui_port}/"],
        "reverse_proxy": True, "skip_provider_button": True,
        "trusted_proxy_ips": ["127.0.0.1/32", "::1/128"],
        "cookie_name": "__Host-ma_session", "cookie_secure": True,
        "cookie_httponly": True, "cookie_samesite": "lax", "cookie_path": "/",
        # OCI refresh can retain an ID token whose at_hash names the previous
        # access token. Require a new authorization flow instead of weakening
        # the identity service's token-pair verification. Pilot sessions are
        # deliberately short until refresh interoperability is qualified.
        "cookie_expire": "20m", "cookie_refresh": "0",
        # The encrypted session store is pod-local and bounded.
        "session_store_type": "redis", "redis_connection_url": "redis://127.0.0.1:6379/0",
        "cookie_csrf_per_request": True, "cookie_csrf_expire": "10m",
        # The private identity service verifies UserInfo when OCI ID tokens omit
        # email. It removes this access token before API/UI/log forwarding.
        "pass_access_token": True, "pass_authorization_header": True,
        "pass_basic_auth": False, "pass_user_headers": True,
        "skip_auth_strip_headers": True, "request_logging": False,
        "auth_logging": False, "standard_logging": True,
        "ping_path": "/oauth2/ping", "ready_path": "/oauth2/ready",
        "session_cookie_minimal": False,
    }
    if metadata:
        # Use advertised issuer with explicitly domain-validated endpoints when
        # its origin serialization differs from the administrative URL.
        settings.update(oidc_issuer_url=metadata["issuer"], skip_oidc_discovery=True,
                        login_url=metadata["authorization_endpoint"],
                        redeem_url=metadata["token_endpoint"],
                        oidc_jwks_url=metadata["jwks_uri"],
                        profile_url=metadata["userinfo_endpoint"])
    return settings


def toml(settings):
    return "\n".join(k + " = " + json.dumps(v) for k, v in settings.items()) + "\n"


def resources(config, namespace="ma-system", ui_service="ma-identity", ui_port=8080, metadata=None, cookie_secret=None):
    subnets = validate(config)
    labels = {"app.kubernetes.io/name": NAME}
    def obj(kind, name=NAME, api="v1", **fields):
        return {"apiVersion": api, "kind": kind, "metadata": {"name": name, "namespace": namespace, "labels": labels}, **fields}
    host = config["public_hostname"]
    caddy = """{
    admin off
    http_port 8080
    https_port 8443
    email ACME_EMAIL
    acme_ca https://acme-v02.api.letsencrypt.org/directory
    servers {
        protocols h1
        timeouts {
            read_body_idle -1s
            write_idle -1s
        }
    }
}
http://:8081 {
    handle /ready {
        rewrite * /oauth2/ready
        reverse_proxy 127.0.0.1:4180
    }
    respond 404
}
PUBLIC_HOST {
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
        -Server
    }
    reverse_proxy 127.0.0.1:4180 {
        header_up -Authorization
        header_up -X-Forwarded-User
        header_up -X-Forwarded-Email
        header_up -X-Forwarded-Preferred-Username
        header_up -X-Forwarded-Groups
        header_up -X-Forwarded-Access-Token
        header_up -X-Auth-Request-User
        header_up -X-Auth-Request-Email
        header_up -X-Auth-Request-Access-Token
    }
}
""".replace("ACME_EMAIL", config["acme_email"]).replace("PUBLIC_HOST", host)
    settings = oauth_config(config, ui_service, namespace, ui_port, metadata)
    cm = obj("ConfigMap", data={"Caddyfile": caddy, "oauth2-proxy.cfg": toml(settings)})
    secret = obj("Secret", type="Opaque", stringData={
        "client-id": config["oidc_client_id"], "client-secret": config["oidc_client_secret"],
        "cookie-secret": cookie_secret or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()})
    pvc = obj("PersistentVolumeClaim", name=NAME + "-tls", spec={"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}})
    security = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "readOnlyRootFilesystem": True}
    mounts = [{"name": "config", "mountPath": "/etc/edge", "readOnly": True}]
    containers = [
        {"name": "caddy", "image": config.get("caddy_image", DEFAULT_CADDY),
         "command": ["/tools/caddy"],
         "args": ["run", "--config", "/etc/edge/Caddyfile", "--adapter", "caddyfile"],
         "ports": [{"name": "http", "containerPort": 8080}, {"name": "https", "containerPort": 8443}],
         "env": [{"name": "XDG_DATA_HOME", "value": "/data"}, {"name": "XDG_CONFIG_HOME", "value": "/config"}],
         "volumeMounts": mounts + [{"name": "tls", "mountPath": "/data"}, {"name": "caddy-config", "mountPath": "/config"}, {"name": "caddy-bin", "mountPath": "/tools", "readOnly": True}],
         "securityContext": security, "readinessProbe": {"httpGet": {"path": "/ready", "port": 8081}, "initialDelaySeconds": 5, "periodSeconds": 5},
         "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}}},
        {"name": "oauth2-proxy", "image": config.get("oauth2_proxy_image", DEFAULT_PROXY),
         "args": ["--config=/etc/edge/oauth2-proxy.cfg"],
         "env": [{"name": env, "valueFrom": {"secretKeyRef": {"name": NAME, "key": key}}} for env, key in
                 [("OAUTH2_PROXY_CLIENT_ID", "client-id"), ("OAUTH2_PROXY_CLIENT_SECRET", "client-secret"), ("OAUTH2_PROXY_COOKIE_SECRET", "cookie-secret")]],
         "volumeMounts": mounts, "securityContext": security,
         "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}}},
        {"name": "session-store", "image": DEFAULT_SESSION_STORE,
         "command": ["redis-server"],
         "args": ["--bind", "127.0.0.1", "--protected-mode", "yes", "--port", "6379",
                  "--save", "", "--appendonly", "no", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction"],
         "securityContext": security,
         "readinessProbe": {"exec": {"command": ["redis-cli", "-h", "127.0.0.1", "ping"]}, "periodSeconds": 5},
         "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "250m", "memory": "128Mi"}}},
    ]
    digest = hashlib.sha256(json.dumps(cm["data"], sort_keys=True).encode()).hexdigest()
    deployment = obj("Deployment", api="apps/v1", spec={
        "replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": labels},
        "template": {"metadata": {"labels": labels, "annotations": {"checksum/config": digest, "checksum/credentials": hashlib.sha256(json.dumps(secret["stringData"], sort_keys=True).encode()).hexdigest()}}, "spec": {
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": "michelangelo-registry"}],
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
            "nodeSelector": config.get("edge_node_selector", {"workload.oci.oracle.com/pool": "system"}), "containers": containers,
            # The official image binary has a privileged-port file capability.
            # A plain copy omits that xattr so it executes with dropped Linux
            # capabilities and no privilege escalation on ports 8080/8443.
            "initContainers": [{"name": "caddy-unprivileged-binary", "image": config.get("caddy_image", DEFAULT_CADDY),
                "command": ["/bin/sh", "-c", "cp /usr/bin/caddy /tools/caddy && chmod 0755 /tools/caddy"],
                "securityContext": security, "volumeMounts": [{"name": "caddy-bin", "mountPath": "/tools"}],
                "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "64Mi"}}}],
            "volumes": [{"name": "config", "configMap": {"name": NAME}}, {"name": "tls", "persistentVolumeClaim": {"claimName": NAME + "-tls"}}, {"name": "caddy-config", "emptyDir": {}}, {"name": "caddy-bin", "emptyDir": {}}]}}})
    annotations = {
        "oci.oraclecloud.com/load-balancer-type": "lb",
        "oci.oraclecloud.com/security-rule-management-mode": "None",
        "oci.oraclecloud.com/reserved-ips": config["reserved_public_ip"],
        "service.beta.kubernetes.io/oci-load-balancer-shape": "flexible",
        "service.beta.kubernetes.io/oci-load-balancer-shape-flex-min": "10",
        "service.beta.kubernetes.io/oci-load-balancer-shape-flex-max": "100",
        "service.beta.kubernetes.io/oci-load-balancer-backend-protocol": "TCP",
    }
    annotations.update({"service.beta.kubernetes.io/oci-load-balancer-subnet" + str(i + 1): subnet for i, subnet in enumerate(subnets)})
    service = obj("Service", spec={"type": "LoadBalancer", "selector": labels, "ports": [
        {"name": "http", "port": 80, "targetPort": 8080, "protocol": "TCP"},
        {"name": "https", "port": 443, "targetPort": 8443, "protocol": "TCP"}]})
    service["metadata"]["annotations"] = annotations
    return [secret, cm, pvc, deployment, service]


def install(config, kubectl=None, namespace="ma-system", ui_service="ma-identity", ui_port=8080):
    """Apply without logging credentials; retain signing secret on reinstalls."""
    command = list(kubectl or ["kubectl"])
    validate(config)
    metadata = discover(config)
    verify_signing_keys(metadata)
    current = subprocess.run(command + ["-n", namespace, "get", "secret", NAME, "-o", "json", "--ignore-not-found"], text=True, capture_output=True, check=True)
    cookie = None
    if current.stdout.strip():
        existing = json.loads(current.stdout)
        cookie = base64.b64decode(existing["data"]["cookie-secret"]).decode()
    payload = {"apiVersion": "v1", "kind": "List", "items": resources(config, namespace, ui_service, ui_port, metadata, cookie)}
    result = subprocess.run(command + ["apply", "-f", "-"], input=json.dumps(payload), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Browser gateway apply failed; inspect Kubernetes events without printing Secrets")
    subprocess.run(command + ["-n", namespace, "rollout", "status", "deployment/" + NAME, "--timeout=10m"], check=True)
    return "https://" + config["public_hostname"] + "/"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_public(config, opener=None):
    """Probe actual public HTTPS using normal certificate verification.

This proves unauthenticated protection/redirects and that no application route
is public. An interactive IAM login is a separate acceptance step.
"""
    validate(config)
    client = opener or build_opener(NoRedirect())
    origin = "https://" + config["public_hostname"]
    results = []
    iam_redirect_seen = False
    for path in ("/", "/api/v2/projects", "/pilot-logs/example/run", "/config.json", "/healthz"):
        request = Request(origin + path, headers={"X-Forwarded-User": config["authorized_emails"][0], "X-Forwarded-Email": config["authorized_emails"][0], "Authorization": "Bearer forged-token", "Cookie": "__Host-ma_session=forged-session"})
        try:
            response = client.open(request, timeout=30)
        except HTTPError as error:
            response = error
        status = response.code
        location = response.headers.get("Location", "")
        cookies = response.headers.get_all("Set-Cookie") or []
        response.close()
        if status not in (302, 303, 401, 403):
            raise RuntimeError("Unauthenticated application route accessible or unhealthy: " + path)
        if status in (302, 303):
            parsed = urlsplit(urljoin(origin, location))
            issuer_host = urlsplit(config["oidc_issuer"]).hostname
            if parsed.scheme != "https" or parsed.hostname not in (config["public_hostname"], issuer_host):
                raise RuntimeError("Unexpected login redirect origin for " + path)
            if parsed.hostname == issuer_host:
                iam_redirect_seen = True
                query = parse_qs(parsed.query)
                if query.get("redirect_uri") != [origin + "/oauth2/callback"] or not query.get("state") or query.get("code_challenge_method") != ["S256"] or not query.get("nonce"):
                    raise RuntimeError("Login redirect is missing callback, state, PKCE, or nonce")
                if query.get("client_id") != [config["oidc_client_id"]]:
                    raise RuntimeError("Login redirect used unexpected OAuth client")
                if query.get("response_type") != ["code"] or not {"openid", "email", "profile"}.issubset(set(query.get("scope", [""])[0].split())):
                    raise RuntimeError("Login redirect uses unexpected OAuth flow or scopes")
        for cookie in cookies:
            if "secure" not in cookie.lower() or "httponly" not in cookie.lower() or "samesite=lax" not in cookie.lower():
                raise RuntimeError("Login cookie lacks required browser protections")
        results.append({"path": path, "status": status, "protected": True})
    if not iam_redirect_seen:
        raise RuntimeError("Public routes denied but no functioning OCI IAM login redirect observed")
    return {"public_https_verified": True, "unauthenticated_routes": results, "interactive_iam_login_verified": False}
