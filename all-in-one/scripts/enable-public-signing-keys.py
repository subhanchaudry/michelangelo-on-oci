#!/usr/bin/env python3
"""Atomically enable only public signing keys, using the Terraform job identity.

--check-only performs signed GETs only. The opt-out preserves the current flag.
Never prints Settings values (except this boolean), credentials, response bodies,
auth headers or environment values. No destroy action reverses the domain flag.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

SDK_VERSION = "2.187.1"
METADATA = {"meta", "resourceVersion", "idcsLastModifiedBy", "idcsLastUpgradedInRelease"}
ENV_NAMES = ["OCI_AUTH", "TF_VAR_auth", "OCI_RESOURCE_PRINCIPAL_VERSION",
             "OCI_RESOURCE_PRINCIPAL_RPST", "OCI_RESOURCE_PRINCIPAL_PRIVATE_PEM",
             "OCI_USE_OBO_TOKEN", "TF_VAR_use_obo_token", "OCI_OBO_TOKEN", "TF_VAR_obo_token",
             "OCI_OBO_TOKEN_PATH", "TF_VAR_obo_token_path", "OCI_CONFIG_FILE",
             "OCI_USER_OCID", "TF_VAR_user_ocid", "OCI_FINGERPRINT", "TF_VAR_fingerprint",
             "use_obo_token", "obo_token", "obo_token_path",
             "OCI_use_obo_token", "OCI_obo_token", "OCI_obo_token_path", "TF_HOME_OVERRIDE"]


def env(name, default=""):
    return os.environ.get("TF_VAR_" + name, os.environ.get("OCI_" + name.upper(), default))


def auxiliary_env(name, default=""):
    # Provider utils.GetEnvSettingWithDefault preserves lower-case names and
    # checks unprefixed keys too; its OBO path is separate from schema defaults.
    for key in ("TF_VAR_" + name, "OCI_" + name, name):
        value = os.environ.get(key)
        if value:
            return value
    return default


def install_sdk_if_needed():
    try:
        import oci
        if oci.__version__ == SDK_VERSION:
            return None
    except ImportError:
        pass
    if os.environ.get("MA_SETTINGS_SDK_READY") == "1":
        raise RuntimeError("Pinned OCI SDK could not be loaded")
    with tempfile.TemporaryDirectory(prefix="ma-settings-sdk-") as directory:
        result = subprocess.run([sys.executable, "-m", "venv", directory], capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("Python virtual environment initialization failed")
        python = str(Path(directory) / "bin/python")
        result = subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
            "oci==" + SDK_VERSION], capture_output=True, timeout=300)
        if result.returncode:
            raise RuntimeError("Pinned official OCI SDK installation failed")
        child_env = dict(os.environ, MA_SETTINGS_SDK_READY="1")
        return subprocess.run([python, str(Path(__file__).resolve()), *sys.argv[1:]], env=child_env, timeout=180).returncode


def signer():
    import oci
    mode = env("auth", "ResourcePrincipal" if os.environ.get("OCI_RESOURCE_PRINCIPAL_VERSION") else "APIKey").lower()
    config = {}
    if mode == "resourceprincipal":
        signing = oci.auth.signers.get_resource_principals_signer()
    elif mode == "instanceprincipal":
        signing = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    elif mode in ("apikey", "securitytoken"):
        profile = env("config_file_profile", "DEFAULT") or "DEFAULT"
        try:
            location = os.environ.get("OCI_CONFIG_FILE") or (
                str(Path(os.environ["TF_HOME_OVERRIDE"]) / ".oci/config")
                if os.environ.get("TF_HOME_OVERRIDE") else oci.config.DEFAULT_LOCATION)
            config = oci.config.from_file(location, profile)
        except oci.exceptions.ConfigFileNotFound:
            # Resource Manager may supply all signer fields through the same
            # TF_VAR_/OCI_ environment inputs used by the Terraform provider.
            config = {}
        mapping = {"tenancy": "tenancy_ocid", "user": "user_ocid", "fingerprint": "fingerprint",
                   "key_file": "private_key_path", "pass_phrase": "private_key_password"}
        for key, variable in mapping.items():
            value = env(variable)
            if value:
                config[key] = value
        content = env("private_key").replace("\\n", "\n") or config.get("key_content")
        if mode == "securitytoken":
            token = Path(config["security_token_file"]).expanduser().read_text().strip()
            key = oci.signer.load_private_key_from_file(config["key_file"], config.get("pass_phrase"))
            signing = oci.auth.signers.SecurityTokenSigner(token, key)
        else:
            signing = oci.signer.Signer(config["tenancy"], config["user"], config["fingerprint"],
                config.get("key_file"), pass_phrase=config.get("pass_phrase"), private_key_content=content)
    else:
        raise RuntimeError("Unsupported Terraform job authentication mode")
    use_obo = auxiliary_env("use_obo_token", "false").lower() == "true"
    if use_obo:
        path = auxiliary_env("obo_token_path")
        obo = Path(path).read_text().strip() if path else auxiliary_env("obo_token")
        if not obo:
            raise RuntimeError("Delegated job authentication lacks its OBO token")
        signing = delegated(signing, obo)
    return signing, mode, use_obo


def delegated(base, token):
    """Mirror provider9.8's signed opc-obo-token interceptor, without new grants."""
    import oci
    class DelegatedSigner(oci.signer.AbstractBaseSigner):
        def __call__(self, request, enforce_content_headers=True):
            request.headers["opc-obo-token"] = token
            base(request, enforce_content_headers=enforce_content_headers)
            self.create_signers(base.api_key, base.private_key,
                ["date", "(request-target)", "host", "opc-obo-token"],
                ["content-length", "content-type", "x-content-sha256"])
            return super().__call__(request, enforce_content_headers=enforce_content_headers)
    return DelegatedSigner()


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def run(args):
    # Use the HTTP implementation shipped/tested with the pinned official SDK,
    # matching its signer and avoiding a second runner dependency.
    from oci._vendor import requests
    parsed = urlsplit(args.domain_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise RuntimeError("Identity domain must be an HTTPS origin")
    signing, mode, obo = signer()
    print(json.dumps({"settings_auth_preflight": True, "signer_mode": mode, "obo_delegation": obo,
        "auth_environment_names_present": [k for k in ENV_NAMES if k in os.environ],
        "check_only": args.check_only}), flush=True)
    session = requests.Session()
    session.auth = signing
    session.trust_env = False
    target = args.domain_url.rstrip("/") + "/admin/v1/Settings/Settings"
    def get():
        response = session.get(target + "?attributeSets=all", timeout=(10, 30), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError("Signed Settings GET returned HTTP " + str(response.status_code))
        value = response.json()
        if value.get("id") != "Settings" or not isinstance(value.get("signingCertPublicAccess"), bool):
            raise RuntimeError("Unexpected Settings identity/public-key flag")
        return value, response.headers.get("ETag")
    before, version = get()
    report = {"signer_mode": mode, "obo_delegation": obo,
              "auth_environment_names_present": [k for k in ENV_NAMES if k in os.environ],
              "check_only": args.check_only, "signingCertPublicAccess": before["signingCertPublicAccess"],
              "setting_fields": len(before), "patched": False}
    if args.check_only or args.enable == "false" or before["signingCertPublicAccess"]:
        report["effective_changed_fields"] = []
        print(json.dumps(report))
        return
    # Atomic path update: no Settings replacement, no other writable field copied.
    body = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "signingCertPublicAccess", "value": True}]}
    headers = {"Content-Type": "application/scim+json"}
    if version:
        headers["If-Match"] = version
    response = session.patch(target, json=body, headers=headers, timeout=(10, 30), allow_redirects=False)
    if response.status_code not in (200, 204):
        raise RuntimeError("Signing-key-only PATCH returned HTTP " + str(response.status_code))
    after, _ = get()
    effective = sorted(k for k in set(before) | set(after)
        if k not in METADATA and canonical(before.get(k)) != canonical(after.get(k)))
    if not after["signingCertPublicAccess"] or any(k != "signingCertPublicAccess" for k in effective):
        raise RuntimeError("Signing-key PATCH verification detected unexpected Settings field differences: " + ", ".join(effective))
    report.update(patched=True, signingCertPublicAccess=True, effective_changed_fields=effective)
    print(json.dumps(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-url", default=os.environ.get("MA_IDENTITY_DOMAIN_URL"), required=not os.environ.get("MA_IDENTITY_DOMAIN_URL"))
    parser.add_argument("--enable", choices=("true", "false"), default=os.environ.get("MA_ENABLE_SIGNING_KEYS", "true"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = install_sdk_if_needed()
        if result is not None:
            raise SystemExit(result)
        run(args)
    except Exception as error:
        # Never expose request objects, SDK exceptions, bodies or private paths.
        print(json.dumps({"settings_automation": "failed", "error_type": type(error).__name__,
                          "reason": str(error) if type(error) is RuntimeError else "Inspect signer/configuration without printing credentials"}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
