"""Load operator-owned recovery connections without changing site authority."""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat

PROFILE_NAME = 'recovery-connections.json'
BINDINGS = ('cluster_id', 'region', 'object_storage_namespace', 'oidc_issuer', 'oidc_client_id')
CONNECTIONS = (
    'mysql_host', 'mysql_expected_hostname', 'mysql_ca_pem', 'mysql_username', 'mysql_password',
    'artifact_bucket', 'artifact_access_key', 'artifact_secret_key',
    'runtime_artifact_access_key', 'runtime_artifact_secret_key',
)
MAX_BYTES = 65536


def compose(config, profile):
    if not isinstance(profile, dict) or set(profile) != {'format', 'recipient', 'connections'}:
        raise RuntimeError('Recovery connection profile has unexpected fields')
    if profile['format'] != 'michelangelo-recovery-connections-v1':
        raise RuntimeError('Recovery connection profile format is unsupported')
    binding = profile['recipient']
    if not isinstance(binding, dict) or set(binding) != set(BINDINGS):
        raise RuntimeError('Recovery recipient binding is incomplete')
    if any(not isinstance(binding[key], str) or not binding[key] or binding[key] != config.get(key) for key in BINDINGS):
        raise RuntimeError('Recovery profile does not belong to this recipient')
    connections = profile['connections']
    if not isinstance(connections, dict) or set(connections) != set(CONNECTIONS):
        raise RuntimeError('Recovery connections are incomplete or contain unsupported settings')
    if any(not isinstance(value, str) or not value or '\x00' in value for value in connections.values()):
        raise RuntimeError('Recovery connections contain invalid values')
    try:
        address = ipaddress.ip_address(connections['mysql_host'])
    except ValueError:
        raise RuntimeError('Recovery database requires a private IP address') from None
    networks = tuple(ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if address.version != 4 or not any(address in network for network in networks):
        raise RuntimeError('Recovery database requires a private IPv4 address')
    hostname = connections['mysql_expected_hostname']
    if len(hostname) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in hostname.split('.')):
        raise RuntimeError('Recovery database hostname is invalid')
    # Root-owned profiles may change connections, never cluster, browser, image
    # registry, namespace, network, or regional authority.
    return {**config, **connections}


def load(config, work):
    """Missing profile preserves ordinary installation; invalid profiles fail closed."""
    path = Path(work) / PROFILE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return config, False
    except OSError:
        raise RuntimeError('Cannot safely open the recovery connection profile') from None
    try:
        info = os.fstat(fd)
        parent = path.parent.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise RuntimeError('Recovery connection profile must be a root-owned 0600 regular file')
        if parent.st_uid != 0 or parent.st_mode & 0o022:
            raise RuntimeError('Recovery profile directory must be owned and writable only by root')
        if info.st_size > MAX_BYTES:
            raise RuntimeError('Recovery connection profile is too large')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise RuntimeError('Recovery connection profile is too large')
        try:
            profile = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError('Recovery connection profile is not valid JSON') from None
        return compose(config, profile), True
    finally:
        if fd is not None:
            os.close(fd)
