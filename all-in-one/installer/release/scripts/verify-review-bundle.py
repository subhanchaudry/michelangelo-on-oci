#!/usr/bin/env python3
"""Verify an extracted review bundle offline; never reads operator state or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def verify(root):
    root = root.resolve()
    manifest = json.loads((root/'SOURCE-MANIFEST.json').read_text())
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Bundle must not contain symlinks')
    if actual != set(manifest) | {'SOURCE-MANIFEST.json'}:
        raise ValueError('Bundle contains missing or unmanifested files')
    for name, checksum in manifest.items():
        path = (root/name).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('Invalid path or checksum: ' + name)
        if path.name in ('terraform.tfvars', '.env') or any(s in path.name for s in ('.tfstate', '.tfplan')) or '.terraform' in path.parts:
            raise ValueError('Forbidden private/generated file: ' + name)
    required = {'DEPENDENCIES.json', 'platform/releases/v0.11.0/manifest.json',
                'scripts/build-upstream-release.py', 'scripts/prepare-demo-image-context.py',
                'scripts/package-upstream-lifecycle.py', 'scripts/verify-review-bundle.py',
                'platform/serving/lifecycle/catalog.json'}
    if not required <= actual:
        raise ValueError('Missing required package entrypoints')
    links = 0
    for name in manifest:
        if not name.endswith('.md'):
            continue
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', (root/name).read_text()):
            target = target.strip().strip('<>')
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            path = ((root/name).parent/unquote(url.path)).resolve() if url.path else root/name
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('Broken or external local link in ' + name + ': ' + target)
            if url.fragment and path.suffix == '.md':
                headings = re.findall(r'^#{1,6}\s+(.+?)\s*#*$', path.read_text(), re.M)
                anchors = {re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings}
                if unquote(url.fragment) not in anchors:
                    raise ValueError('Broken heading link in ' + name + ': ' + target)
            links += 1
    release = root/'platform/releases/v0.11.0'
    m = json.loads((release/'manifest.json').read_text())
    if hashlib.sha256((release/m['patch']['file']).read_bytes()).hexdigest() != m['patch']['sha256']:
        raise ValueError('Integration patch does not match release manifest')
    catalog = json.loads((root/'platform/serving/lifecycle/catalog.json').read_text())
    if catalog['status'] != 'requires-site-validation' or catalog['handoff']['verified_for_current_release']:
        raise ValueError('Site acceptance must not be inherited')
    if any(m['acceptance'] != 'requires-site-validation' or any(p['deployed'] for p in m['pipelines']) for m in catalog['models']):
        raise ValueError('Model/pipeline site acceptance must be reset')
    return {'verified_payload_files': len(manifest), 'local_links_verified': links,
            'patch_checksum_verified': True, 'site_acceptance_reset': True,
            'secret_scan': 'separate full-tree scan required', 'production_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(verify(args.root)))


if __name__ == '__main__': main()
