#!/usr/bin/env python3
"""Assemble a reviewed Michelangelo release without using a developer checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=ROOT/'platform/releases/v0.11.0')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    release = args.release.resolve()
    manifest = json.loads((release/'manifest.json').read_text())
    if args.destination.exists():
        parser.error('Destination must not exist; existing source is never reset')
    patch = release/manifest['patch']['file']
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest['patch']['sha256']:
        parser.error('Reviewed source patch checksum mismatch')
    subprocess.run(['git','clone','--branch',manifest['upstream_tag'],'--single-branch',manifest['repository'],str(args.destination)],check=True)
    git = ['git','-C',str(args.destination)]
    actual = subprocess.check_output(git+['rev-parse','HEAD'],text=True).strip()
    if actual != manifest['upstream_commit']:
        raise RuntimeError('Upstream tag does not resolve to the reviewed commit')
    subprocess.run(git+['apply','--check',str(patch)],check=True)
    subprocess.run(git+['apply',str(patch)],check=True)
    subprocess.run(git+['diff','--check'],check=True)
    print(json.dumps({'source':str(args.destination.resolve()),'release':manifest['release'],'status':'assembled; not deployed'}))

if __name__ == '__main__':
    main()
