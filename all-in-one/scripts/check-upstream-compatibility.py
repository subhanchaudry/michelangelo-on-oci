#!/usr/bin/env python3
"""Read-only patch applicability assessment of the pinned 0.13 upstream tree.

This does not apply overlays, select a release, or establish runtime compatibility.
Exit 1 means one or more patch sections need review; exit 2 means invalid inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def git(source, *args):
    return subprocess.run(
        ['git', '-C', str(source), *args], text=True, capture_output=True,
        check=False,
    )


def assess(source):
    target = json.loads((ROOT / '.github/release-history/release-target-0.13.0.json').read_text())
    revision = git(source, 'rev-parse', 'HEAD')
    if revision.returncode or revision.stdout.strip() != target['upstream_commit']:
        raise ValueError('Source HEAD does not match the pinned upstream commit')
    status = git(source, 'status', '--porcelain', '--untracked-files=all')
    if status.returncode or status.stdout.strip():
        raise ValueError('Use a clean, unmodified upstream checkout')
    manifest_path = ROOT / 'all-in-one/installer/sources/v0.12.0/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    patch = manifest_path.parent / manifest['patch']['file']
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    if digest != manifest['patch']['sha256']:
        raise ValueError('Legacy patch checksum does not match its manifest')
    paths = re.findall(r'^diff --git a/(.+) b/\1$', patch.read_text(), re.MULTILINE)
    if not paths or len(paths) != len(set(paths)):
        raise ValueError('Patch path inventory is empty or ambiguous')
    rows = []
    for path in paths:
        result = git(source, 'apply', '--check', '--include=' + path, str(patch))
        rows.append({
            'path': path,
            'applies': result.returncode == 0,
            'diagnostic': result.stderr.strip(),
        })
    whole = git(source, 'apply', '--check', str(patch))
    after = git(source, 'status', '--porcelain', '--untracked-files=all')
    if after.returncode or after.stdout != status.stdout:
        raise ValueError('Source status changed during assessment')
    return {
        'upstream_commit': target['upstream_commit'],
        'candidate': target['candidate'],
        'legacy_patch_sha256': digest,
        'patch_paths': len(rows),
        'applicable_paths': sum(row['applies'] for row in rows),
        'conflicting_paths': sum(not row['applies'] for row in rows),
        'whole_patch_applies': whole.returncode == 0,
        'whole_patch_diagnostic': whole.stderr.strip(),
        'source_unchanged': True,
        'overlay_validation': 'not_performed',
        'runtime_validation': 'not_performed',
        'paths': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = assess(args.source.resolve())
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['whole_patch_applies'] else 1


if __name__ == '__main__':
    sys.exit(main())
