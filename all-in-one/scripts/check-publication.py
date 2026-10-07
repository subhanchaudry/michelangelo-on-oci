#!/usr/bin/env python3
"""Reject common secrets and private deployment files before commit or packaging.

This is a publication guard, not a substitute for review or an image security scan.
Only paths and rule names are reported; matching values are never printed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE' + rb' KEY-----'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'aws-access-key': re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
    'jwt': re.compile(rb'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b'),
    'personal-home-path': re.compile(rb'/(?:Users|home)/[a-zA-Z][a-zA-Z0-9._-]+/'),
    'identity-domain-id': re.compile(rb'idcs-[a-fA-F0-9]{32}\.'),
}
# Standard builder account paths are deployment code, not personal home directories.
HOME_ALLOW = (b'/home/ubuntu/',)


def check(files):
    errors = []
    for name, data in sorted(files.items()):
        p = Path(name)
        if p.is_absolute() or '..' in p.parts:
            errors.append((name, 'unsafe-path'))
        private = ('.git', '.terraform', '.oci', '.ssh', '__pycache__')
        if (any(part in private for part in p.parts)
            or '.tfstate' in p.name or '.tfplan' in p.name
            or p.name.endswith(('.tfvars', '.tfvars.json', '.pem', '.key', '.p12', '.pfx', '.pyc'))
            or p.name.startswith('.env') or 'kubeconfig' in p.name.lower()
            or p.name in ('config.json', 'credentials', 'id_rsa', 'id_ed25519')):
            errors.append((name, 'private-or-generated-file'))
        for rule, pattern in PATTERNS.items():
            matches = pattern.findall(data)
            if rule == 'personal-home-path':
                matches = [m for m in matches if m not in HOME_ALLOW]
            if matches:
                errors.append((name, rule))
        for value in re.findall(rb'\bocid1\.[a-z0-9_.-]{20,}', data):
            if not any(marker in value.lower() for marker in (b'example', b'replace', b'placeholder')):
                errors.append((name, 'site-ocid'))
                break
    if errors:
        raise ValueError(json.dumps({'publication_failures': errors}, indent=2))
    return {'files_checked': len(files), 'publication_guard': 'passed'}


def tree(root):
    files = {}
    for p in root.rglob('*'):
        if '.git' in p.relative_to(root).parts:
            continue
        if p.is_symlink():
            raise ValueError('Symlink in publication tree: ' + str(p.relative_to(root)))
        if p.is_file():
            files[p.relative_to(root).as_posix()] = p.read_bytes()
    return files


def staged(root):
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '-z']).split(b'\0')
    files = {}
    for encoded in filter(None, names):
        name = encoded.decode('utf-8')
        mode = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--stage', '--', name]).split()[0]
        if mode != b'100644' and mode != b'100755':
            raise ValueError('Nonregular Git entry: ' + name)
        files[name] = subprocess.check_output(['git', '-C', str(root), 'show', ':' + name])
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--staged', action='store_true', help='Check complete Git index, not just changed paths')
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.archive:
        with zipfile.ZipFile(args.archive) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError('Duplicate archive entries')
            if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in archive.infolist()):
                raise ValueError('Symlink in archive')
            files = {n: archive.read(n) for n in archive.namelist() if not n.endswith('/')}
    else:
        files = staged(args.root) if args.staged else tree(args.root)
    print(json.dumps(check(files), indent=2))


if __name__ == '__main__':
    main()
