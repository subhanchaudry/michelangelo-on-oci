#!/usr/bin/env python3
"""Prepare/check reviewed site evidence offline. Does not execute live tests."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def template(cluster_uid, source_manifest, images):
    contract = json.loads((ROOT / 'ACCEPTANCE-CONTRACT.json').read_text())
    catalog = json.loads((ROOT / 'installer/release/platform/serving/lifecycle/catalog.json').read_text())
    def pending(names):
        return {name: {'status': 'not-run', 'reviewer': '', 'completed_at': '', 'evidence': []} for name in names}
    return {'schema_version': 1, 'cluster_uid': cluster_uid,
            'source_manifest_sha256': digest(source_manifest), 'images_sha256': digest(images),
            'site_checks': pending(contract['site_checks']),
            'models': {m['demo']: pending(contract['model_checks']) for m in catalog['models']}}


def validate(record, expected, directory):
    for name in ('schema_version', 'cluster_uid', 'source_manifest_sha256', 'images_sha256'):
        if record.get(name) != expected[name]:
            raise ValueError('Evidence belongs to a different site/build: ' + name)
    groups = [('site', record.get('site_checks', {}), expected['site_checks'])]
    groups += [(name, record.get('models', {}).get(name, {}), checks) for name, checks in expected['models'].items()]
    missing = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for group, actual, required in groups:
        for name in required:
            check = actual.get(name, {})
            if check.get('status') != 'passed' or not str(check.get('reviewer', '')).strip() or not check.get('evidence'):
                missing.append(group + '/' + name)
                continue
            completed = datetime.datetime.fromisoformat(check.get('completed_at', '').replace('Z', '+00:00'))
            if completed.tzinfo is None or completed > now:
                raise ValueError('Invalid evidence timestamp: ' + group + '/' + name)
            for evidence in check['evidence']:
                relative = Path(evidence['path'])
                path = (directory / relative).resolve()
                if relative.is_absolute() or not path.is_relative_to(directory.resolve()):
                    raise ValueError('Evidence must be within the site record directory')
                if not path.is_file() or digest(path) != evidence['sha256']:
                    raise ValueError('Missing/changed evidence: ' + str(relative))
    return {'status': 'incomplete' if missing else 'reviewed-engineering-evidence-complete',
            'missing_checks': missing, 'production_ready': False,
            'limitation': 'Checks recorded evidence and reviewer attestations; does not rerun tests or certify their contents.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['init', 'check'])
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--cluster-uid', required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    args = parser.parse_args()
    expected = template(args.cluster_uid, args.source_manifest, args.images)
    if args.mode == 'init':
        args.record.parent.mkdir(parents=True, exist_ok=True)
        with args.record.open('x') as stream:
            json.dump(expected, stream, indent=2)
            stream.write('\n')
        print('Created pending evidence record; no checks have passed.')
        return 0
    report = validate(json.loads(args.record.read_text()), expected, args.record.parent)
    print(json.dumps(report, indent=2))
    return 1 if report['missing_checks'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
