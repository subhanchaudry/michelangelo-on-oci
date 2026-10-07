#!/usr/bin/env python3
"""Persistent application membership behind OCI IAM/OAuth2 Proxy.

Port 8080 protects every browser request and serves the application user API.
Port 8081 resolves verified ID tokens for the private application API. No tokens,
cookies, passwords, request headers or request bodies are written to logs.
"""
import argparse
import base64
import contextlib
import datetime
import http.client
import hashlib
import hmac
import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

import jwt
import pymysql

EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ROLES = {"admin", "operator"}
MAX_BODY = 16 * 1024 * 1024
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade"}
PRIVATE_HEADERS = {"cookie", "x-forwarded-user", "x-forwarded-email", "x-forwarded-groups",
                   "x-forwarded-preferred-username", "x-forwarded-access-token",
                   "x-auth-request-user", "x-auth-request-email", "x-auth-request-access-token",
                   "x-user-name", "x-user-email", "x-user-id", "x-ma-user-id", "x-ma-role"}


class AccessError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def verified_email(claims):
    verified = claims.get("email_verified")
    if not (verified is True or (isinstance(verified, str) and verified == "true")):
        raise AccessError(401, "A verified OCI IAM email is required")
    try:
        return email_address(claims.get("email"))
    except AccessError:
        raise AccessError(401, "A verified OCI IAM email is required") from None


def email_address(value):
    if not isinstance(value, str) or len(value) > 320 or not EMAIL.fullmatch(value):
        raise AccessError(400, "Enter a valid existing OCI IAM email address")
    return value.lower()


def profile(row):
    immutable = row.get("immutable_user_id")
    return {"id": row["id"], "domain_id": row["domain_id"],
            "subject": "idcs-user:" + immutable if immutable else None,
            "email": row["email"], "name": row["display_name"] or row["email"],
            "role": row["role"], "enabled": bool(row["enabled"])}


def immutable_user_id(value):
    """OCI's signed user_id is the identity-domain GUID, not a login name."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", value):
        raise AccessError(401, "A signed immutable OCI IAM user identifier is required")
    identity = uuid.UUID(value).hex
    if identity == "0" * 32:
        raise AccessError(401, "A signed immutable OCI IAM user identifier is required")
    return identity


class Directory:
    def __init__(self, config):
        self.config = config
        self.jwks = jwt.PyJWKClient(config["jwks_uri"], cache_keys=False,
                                   cache_jwk_set=True, lifespan=60, timeout=10)
        self.key_lock = threading.Lock()
        self.profile_lock = threading.Lock()
        self.profile_cache = {}
        self.profile_opener = build_opener(NoRedirect)
        endpoint = config.get("userinfo_endpoint")
        if endpoint:
            parsed, domain = urlsplit(endpoint), urlsplit(config["domain_id"])
            if (parsed.scheme != "https" or parsed.hostname != domain.hostname
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or parsed.fragment or parsed.query):
                raise ValueError("UserInfo must use the selected HTTPS identity domain")

    @contextlib.contextmanager
    def database(self):
        c = self.config
        connection = pymysql.connect(host=c["mysql_hostname"], port=int(c.get("mysql_port", 3306)),
            user=c["mysql_username"], password=c["mysql_password"], database=c["mysql_database"],
            charset="utf8mb4", autocommit=False, connect_timeout=10, read_timeout=15, write_timeout=15,
            cursorclass=pymysql.cursors.DictCursor, ssl_ca=c["mysql_ca_file"],
            ssl_verify_cert=True, ssl_verify_identity=True)
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.database() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS app_users (
                id CHAR(36) PRIMARY KEY, domain_id VARCHAR(255) NOT NULL,
                subject VARCHAR(255) NULL, immutable_user_id VARCHAR(64) NULL,
                email VARCHAR(320) NOT NULL,
                display_name VARCHAR(255) NOT NULL DEFAULT '',
                role ENUM('admin','operator') NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE,
                invited_by CHAR(36) NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY identity_email(domain_id, email),
                UNIQUE KEY immutable_identity(domain_id, immutable_user_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin""")
            cursor.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s AND column_name='immutable_user_id'", ("app_users",))
            if cursor.fetchone() is None:
                # Existing identities remain inaccessible until a separately
                # reviewed immutable-ID migration. Never migrate by email.
                cursor.execute("ALTER TABLE app_users ADD COLUMN immutable_user_id VARCHAR(64) NULL, ADD UNIQUE KEY immutable_identity(domain_id, immutable_user_id)")
            cursor.execute("SELECT INDEX_NAME FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=%s AND index_name='identity_subject' LIMIT 1", ("app_users",))
            if cursor.fetchone() is not None:
                # The OIDC login subject is mutable profile metadata only.
                # Immutable identity uniqueness must already exist above.
                cursor.execute("ALTER TABLE app_users DROP INDEX identity_subject")
            cursor.execute("""CREATE TABLE IF NOT EXISTS app_user_audit (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, actor_id CHAR(36) NULL,
                actor_subject VARCHAR(255) NULL, action VARCHAR(32) NOT NULL,
                target_id CHAR(36) NOT NULL, details JSON NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin""")
            cursor.execute("SELECT COUNT(*) AS members FROM app_users")
            if cursor.fetchone()["members"]:
                return
            admins = {email_address(e) for e in self.config["bootstrap_admin_emails"]}
            operators = {email_address(e) for e in self.config.get("initial_operator_emails", [])} - admins
            if not admins:
                raise RuntimeError("At least one bootstrap application administrator is required")
            for role, emails in (("admin", admins), ("operator", operators)):
                for email in sorted(emails):
                    # Preserve all existing roles, subject bindings and revocations on retries.
                    cursor.execute("INSERT IGNORE INTO app_users(id,domain_id,email,role) VALUES(%s,%s,%s,%s)",
                        (str(uuid.uuid4()), self.config["domain_id"], email, role))

    def claims(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            raise AccessError(401, "An authenticated OCI IAM session is required")
        token = authorization[7:]
        if len(token) > 32768 or token.count(".") != 2:
            raise AccessError(401, "Invalid OCI IAM identity token")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError("Unexpected signing algorithm or key")
            with self.key_lock:
                key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.config["audience"],
                issuer=self.config["issuer"], leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 255:
                raise ValueError("Missing subject")
            claims["immutable_user_id"] = immutable_user_id(claims.get("user_id"))
            if "email" in claims:
                claims["email"] = verified_email(claims)
            elif "email_verified" in claims:
                verified = claims["email_verified"]
                if not (verified is True or (isinstance(verified, str) and verified == "true")):
                    raise ValueError("Explicitly unverified identity")
            audience = claims["aud"]
            if ((isinstance(audience, list) and len(audience) > 1)
                    or "azp" in claims) and claims.get("azp") != self.config["audience"]:
                raise ValueError("Unexpected authorized party")
            return claims
        except Exception:
            raise AccessError(401, "Invalid OCI IAM identity token") from None

    def userinfo(self, authorization, access_token, claims):
        """Retrieve a verified profile; bind it to the already verified ID token."""
        if (not isinstance(access_token, str) or not access_token or len(access_token) > 32768
                or any(character.isspace() for character in access_token)):
            raise AccessError(401, "A verified OCI IAM profile is required")
        expected_hash = claims.get("at_hash")
        if expected_hash is not None:
            actual_hash = base64.urlsafe_b64encode(hashlib.sha256(access_token.encode()).digest()[:16]).rstrip(b"=").decode()
            if not isinstance(expected_hash, str) or not hmac.compare_digest(actual_hash, expected_hash):
                raise AccessError(401, "OCI IAM session token binding failed")
        cache_key = hashlib.sha256((authorization + "\0" + access_token).encode()).hexdigest()
        now = time.time()
        with self.profile_lock:
            cached = self.profile_cache.get(cache_key)
            if cached and cached[0] > now:
                return dict(cached[1])
        endpoint = self.config.get("userinfo_endpoint")
        if not endpoint:
            raise AccessError(401, "A verified OCI IAM profile is required")
        try:
            request = Request(endpoint, headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"})
            with self.profile_opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise ValueError("UserInfo failed")
                body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("UserInfo exceeded size limit")
            information = json.loads(body)
            if not isinstance(information, dict):
                raise ValueError("Unexpected UserInfo response")
            verified = information.get("email_verified")
            print(json.dumps({"event": "identity_userinfo_claims",
                "claim_names": sorted(k for k in information if re.fullmatch(r"[A-Za-z0-9_]{1,64}", k)),
                "email_present": "email" in information,
                "email_verified": verified is True or (isinstance(verified, str) and verified == "true"),
                "subject_matches": information.get("sub") == claims["sub"]}), file=sys.stderr)
            if information.get("sub") != claims["sub"]:
                raise ValueError("UserInfo subject mismatch")
            information["email"] = verified_email(information)
            # Store only public verified profile fields, never credentials.
            profile_claims = {key: information[key] for key in ("sub", "email", "name", "preferred_username") if key in information}
            profile_claims["email_verified"] = True
            expires = min(now + 60, float(claims["exp"]))
            if expires <= now:
                raise ValueError("Expired profile identity")
            with self.profile_lock:
                self.profile_cache = {key: value for key, value in self.profile_cache.items() if value[0] > now}
                if len(self.profile_cache) >= 256:
                    self.profile_cache.pop(next(iter(self.profile_cache)))
                self.profile_cache[cache_key] = (expires, profile_claims)
            return dict(profile_claims)
        except Exception:
            raise AccessError(401, "A verified OCI IAM profile is required") from None

    def resolve(self, authorization, access_token=None):
        claims = self.claims(authorization)
        if "email" not in claims and access_token is not None:
            claims.update(self.userinfo(authorization, access_token, claims))
        email = claims.get("email")
        name = claims.get("name") or claims.get("user_displayname") or claims.get("preferred_username") or email
        if not isinstance(name, str):
            name = email
        if isinstance(name, str):
            name = name[:255]
        domain, subject = self.config["domain_id"], claims["sub"]
        immutable = claims["immutable_user_id"]
        canonical = "idcs-user:" + immutable
        with self.database() as cursor:
            cursor.execute("SELECT * FROM app_users WHERE domain_id=%s AND immutable_user_id=%s FOR UPDATE", (domain, immutable))
            row = cursor.fetchone()
            if row is None:
                if not email:
                    raise AccessError(401, "A verified OCI IAM profile is required before first application sign-in")
                cursor.execute("SELECT * FROM app_users WHERE domain_id=%s AND email=%s FOR UPDATE", (domain, email))
                row = cursor.fetchone()
                # Only a new unbound invitation can bind. A legacy record or
                # different immutable user can never inherit its roles by
                # reusing the same username/email.
                if row is None or not row["enabled"] or row["subject"] is not None or row.get("immutable_user_id") is not None:
                    raise AccessError(403, "Your OCI IAM identity has not been granted application access")
                cursor.execute("UPDATE app_users SET subject=%s,immutable_user_id=%s,display_name=%s WHERE id=%s", (subject, immutable, name, row["id"]))
                self.audit(cursor, row["id"], canonical, "identity_bound", row["id"], {"email": email, "identity_claim": "user_id"})
                row["subject"], row["display_name"] = subject, name
                row["immutable_user_id"] = immutable
            if not row["enabled"]:
                raise AccessError(403, "Your application access has been removed")
            if email and (row["email"] != email or row["display_name"] != name or row["subject"] != subject):
                try:
                    cursor.execute("UPDATE app_users SET subject=%s,email=%s,display_name=%s WHERE id=%s", (subject, email, name, row["id"]))
                except pymysql.err.IntegrityError:
                    raise AccessError(403, "This identity email conflicts with an existing application account") from None
                row["subject"], row["email"], row["display_name"] = subject, email, name
            return profile(row)

    def migrate_verified_legacy_identity(self, member_id, expected_email, expected_subject, verified_user_id):
        """Explicit operator migration after out-of-band OCI identity proof.

        Not exposed through HTTP or invoked during sign-in. The operator must
        supply the previously recorded immutable IAM GUID independently checked
        through OCI DEFAULT, not infer an identity from a reusable email.
        """
        immutable = immutable_user_id(verified_user_id)
        expected_email = email_address(expected_email)
        with self.database() as cursor:
            cursor.execute("SELECT * FROM app_users WHERE id=%s AND domain_id=%s FOR UPDATE", (member_id, self.config["domain_id"]))
            row = cursor.fetchone()
            if (row is None or row["email"] != expected_email or row["subject"] != expected_subject
                    or row["role"] != "admin" or not row["enabled"]):
                raise AccessError(409, "Verified legacy identity does not match the expected active administrator")
            existing = row.get("immutable_user_id")
            if existing is not None:
                if existing != immutable:
                    raise AccessError(409, "Immutable identity is already bound differently")
                return profile(row)
            cursor.execute("UPDATE app_users SET immutable_user_id=%s WHERE id=%s AND immutable_user_id IS NULL", (immutable, row["id"]))
            self.audit(cursor, row["id"], "system:verified-identity-migration", "identity_migrated", row["id"],
                {"identity_claim": "user_id", "legacy_subject": expected_subject, "immutable_user_id": immutable,
                 "authorization_preserved": True})
            row["immutable_user_id"] = immutable
            return profile(row)

    @staticmethod
    def audit(cursor, actor_id, subject, action, target_id, details):
        cursor.execute("INSERT INTO app_user_audit(actor_id,actor_subject,action,target_id,details) VALUES(%s,%s,%s,%s,%s)",
            (actor_id, subject, action, target_id, json.dumps(details)))

    def require_admin(self, cursor, actor):
        cursor.execute("SELECT * FROM app_users WHERE id=%s AND domain_id=%s FOR UPDATE", (actor["id"], self.config["domain_id"]))
        current = cursor.fetchone()
        if current is None or not current["enabled"] or current["role"] != "admin":
            raise AccessError(403, "Application administrator access is required")

    def list_users(self, actor):
        if actor["role"] != "admin":
            raise AccessError(403, "Application administrator access is required")
        with self.database() as cursor:
            self.require_admin(cursor, actor)
            cursor.execute("SELECT * FROM app_users WHERE domain_id=%s ORDER BY email", (self.config["domain_id"],))
            return [profile(row) for row in cursor.fetchall()]

    def create_user(self, actor, payload):
        email, role = email_address(payload.get("email")), payload.get("role", "operator")
        if role not in ROLES:
            raise AccessError(400, "Choose administrator or operator")
        identity = str(uuid.uuid4())
        with self.database() as cursor:
            self.require_admin(cursor, actor)
            try:
                cursor.execute("INSERT INTO app_users(id,domain_id,email,role,invited_by) VALUES(%s,%s,%s,%s,%s)",
                    (identity, self.config["domain_id"], email, role, actor["id"]))
            except pymysql.err.IntegrityError:
                raise AccessError(409, "This email already has an application account; edit or re-enable it") from None
            self.audit(cursor, actor["id"], actor["subject"], "user_created", identity, {"email": email, "role": role})
            cursor.execute("SELECT * FROM app_users WHERE id=%s", (identity,))
            return profile(cursor.fetchone())

    def update_user(self, actor, identity, payload):
        try:
            uuid.UUID(identity)
        except ValueError:
            raise AccessError(400, "Invalid application user") from None
        if not payload or set(payload) - {"role", "enabled"}:
            raise AccessError(400, "Only application role and enabled status may be changed")
        if "role" in payload and payload["role"] not in ROLES:
            raise AccessError(400, "Choose administrator or operator")
        if "enabled" in payload and not isinstance(payload["enabled"], bool):
            raise AccessError(400, "Enabled must be true or false")
        with self.database() as cursor:
            # Lock every active administrator before the actor/target, consistently.
            # Concurrent administrators cannot remove each other or the last admin.
            cursor.execute("SELECT id FROM app_users WHERE domain_id=%s AND role='admin' AND enabled=TRUE ORDER BY id FOR UPDATE", (self.config["domain_id"],))
            admins = {row["id"] for row in cursor.fetchall()}
            self.require_admin(cursor, actor)
            cursor.execute("SELECT * FROM app_users WHERE id=%s AND domain_id=%s FOR UPDATE", (identity, self.config["domain_id"]))
            row = cursor.fetchone()
            if row is None:
                raise AccessError(404, "Application user not found")
            enabled, role = payload.get("enabled", bool(row["enabled"])), payload.get("role", row["role"])
            loses_admin = row["enabled"] and row["role"] == "admin" and (not enabled or role != "admin")
            if identity == actor["id"] and (not enabled or role != "admin"):
                raise AccessError(409, "You cannot remove your own administrator access")
            if loses_admin and admins == {identity}:
                raise AccessError(409, "The last administrator cannot be removed or demoted")
            cursor.execute("UPDATE app_users SET enabled=%s,role=%s WHERE id=%s", (enabled, role, identity))
            self.audit(cursor, actor["id"], actor["subject"], "user_updated", identity, {"enabled": enabled, "role": role})
            row["enabled"], row["role"] = enabled, role
            return profile(row)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MichelangeloIdentity"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, *_args):
        pass

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def body(self, limit=MAX_BODY):
        if self.headers.get("Transfer-Encoding"):
            raise AccessError(400, "Transfer encoding is unsupported")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise AccessError(400, "Invalid request length") from None
        if length < 0 or length > limit:
            raise AccessError(413, "Request is too large")
        return self.rfile.read(length)

    def payload(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise AccessError(415, "Use application/json")
        try:
            payload = json.loads(self.body(limit=4096))
        except (ValueError, UnicodeError):
            raise AccessError(400, "Invalid JSON") from None
        if not isinstance(payload, dict):
            raise AccessError(400, "A JSON object is required")
        return payload

    def csrf(self):
        if self.headers.get("Origin") != self.server.directory.config["public_origin"]:
            raise AccessError(403, "Use the application's same-origin Users page")
        if self.headers.get("X-MA-CSRF") != "1":
            raise AccessError(403, "Missing request protection header")

    def handle_request(self):
        path = urlsplit(self.path).path
        directory = self.server.directory
        if self.server.internal and path in ("/healthz", "/ready") and self.command in ("GET", "HEAD"):
            if path == "/ready":
                with directory.database() as cursor:
                    cursor.execute("SELECT 1")
            return self.send_json(200, {"ready": True})
        if self.server.internal:
            if path != "/internal/resolve" or self.command != "POST":
                raise AccessError(404, "Not found")
            return self.send_json(200, directory.resolve(self.headers.get("Authorization")))
        if path == "/internal" or path.startswith("/internal/"):
            raise AccessError(404, "Not found")
        access_token = self.headers.get("X-Forwarded-Access-Token")
        actor = directory.resolve(self.headers.get("Authorization"), access_token) if access_token else directory.resolve(self.headers.get("Authorization"))
        if path in ("/api/identity/me", "/api/me") and self.command in ("GET", "HEAD"):
            return self.send_json(200, actor)
        prefixes = ("/api/identity/users", "/api/users")
        prefix = next((p for p in prefixes if path == p or path.startswith(p + "/")), None)
        if prefix:
            if path == prefix:
                if self.command == "GET":
                    return self.send_json(200, {"users": directory.list_users(actor)})
                if self.command == "POST":
                    self.csrf()
                    return self.send_json(201, directory.create_user(actor, self.payload()))
            else:
                identity = path[len(prefix) + 1:]
                if self.command in ("PATCH", "DELETE"):
                    self.csrf()
                    payload = self.payload()
                    if self.command == "DELETE":
                        payload = {"enabled": False}
                    return self.send_json(200, directory.update_user(actor, identity, payload))
            raise AccessError(405, "Method not allowed")
        return self.proxy(actor)

    def proxy(self, actor):
        if self.command not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            raise AccessError(405, "Method not allowed")
        if self.command not in ("GET", "HEAD", "OPTIONS"):
            origin = self.headers.get("Origin")
            if origin and origin != self.server.directory.config["public_origin"]:
                raise AccessError(403, "Cross-origin application requests are forbidden")
        upstream = urlsplit(self.server.directory.config["upstream_url"])
        if upstream.scheme != "http" or upstream.path not in ("", "/"):
            raise RuntimeError("Invalid private upstream")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS | PRIVATE_HEADERS | {"host", "content-length"}}
        if not urlsplit(self.path).path.startswith("/api/"):
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        # Identity is resolved independently by the API using the signed token.
        # Do not transport display names or make user-controlled headers roles.
        headers.update({"Host": upstream.netloc, "X-Forwarded-Proto": "https"})
        body = self.body()
        headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(upstream.hostname, upstream.port or 80, timeout=60)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS | {"authorization", "set-cookie", "x-forwarded-access-token", "x-auth-request-access-token"}:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            connection.close()

    def dispatch(self):
        try:
            self.handle_request()
        except AccessError as error:
            self.send_json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            print(json.dumps({"event": "request_failed", "error_type": type(error).__name__}), file=sys.stderr)
            self.send_json(503, {"error": "Application identity service is temporarily unavailable"})

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = dispatch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    config = json.loads(parser.parse_args().config.read_text())
    directory = Directory(config)
    for attempt in range(20):
        try:
            directory.initialize()
            break
        except Exception as error:
            if attempt == 19:
                raise RuntimeError("Identity database initialization failed") from None
            print(json.dumps({"event": "database_wait", "error_type": type(error).__name__}), file=sys.stderr)
            time.sleep(3)
    servers = []
    for port, internal in ((8080, False), (8081, True)):
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        server.daemon_threads = True
        server.directory, server.internal = directory, internal
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print(json.dumps({"event": "identity_ready", "browser_port": 8080, "internal_port": 8081}), flush=True)
    try:
        while True:
            time.sleep(60)
    finally:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
