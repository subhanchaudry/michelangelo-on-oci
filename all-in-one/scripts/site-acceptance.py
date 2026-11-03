#!/usr/bin/env python3
"""Prepare/check reviewed site evidence offline. Does not execute live tests."""
import argparse
import datetime
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PROFILE = 'public-release'
EXCLUDED_SECOND_HUMAN = (
    'iam_second_user_login', 'operator_denied_user_management',
    'remove_revokes_existing_session', 'restore_same_identity',
)


def profile_arguments(parser):
    parser.add_argument('--profile', choices=('migration', PUBLIC_PROFILE), default='migration')
    parser.add_argument('--source-revision', help='Full Git commit of the packaged source')
    parser.add_argument('--package', type=Path, help='Exact all-in-one ZIP deployed for this release')


def from_arguments(args):
    return template(args.cluster_uid, args.source_manifest, args.images,
                    args.profile, args.source_revision, args.package)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def template(cluster_uid, source_manifest, images, profile='migration', source_revision=None, package=None):
    contract = json.loads((ROOT / 'ACCEPTANCE-CONTRACT.json').read_text())
    catalog = json.loads((ROOT / 'installer/release/platform/serving/lifecycle/catalog.json').read_text())
    def pending(names):
        return {name: {'status': 'not-run', 'reviewer': '', 'completed_at': '', 'evidence': []} for name in names}
    record = {'schema_version': 1, 'cluster_uid': cluster_uid,
            'source_manifest_sha256': digest(source_manifest), 'images_sha256': digest(images),
            'site_checks': pending(contract['site_checks']),
            'models': {m['demo']: pending(contract['model_checks']) for m in catalog['models']}}
    if profile == PUBLIC_PROFILE:
        if not source_revision or not re.fullmatch('[0-9a-f]{40}', source_revision) or package is None:
            raise ValueError('Public release requires a full source revision and the deployed package')
        with zipfile.ZipFile(package) as archive:
            if archive.read('SOURCE-MANIFEST.json') != source_manifest.read_bytes():
                raise ValueError('Source manifest does not belong to the supplied package')
            manifest = json.loads(source_manifest.read_text())
            files = manifest.get('files')
            if manifest.get('profile') != 'all-in-one' or not isinstance(files, dict) or not files:
                raise ValueError('Public release requires a complete all-in-one manifest')
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(files) | {'SOURCE-MANIFEST.json'}:
                raise ValueError('Package inventory differs from its manifest')
            for name, checksum in files.items():
                if Path(name).is_absolute() or '..' in Path(name).parts or hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                    raise ValueError('Package payload missing or changed: ' + name)
        record.update(schema_version=2, profile=profile, source_revision=source_revision,
                      package_sha256=digest(package), excluded_checks={})
        for name in EXCLUDED_SECOND_HUMAN:
            del record['site_checks'][name]
            record['excluded_checks']['site/' + name] = {
                'status': 'excluded', 'reason': 'Second-human exercise excluded by release owner; automated authorization regressions remain required.'}
        for checks in record['models'].values():
            del checks['studio_logs_outputs_resources']
            checks.update(pending(['studio_logs_outputs']))
        record['upstream_limitations'] = ['Studio resource views marked Coming soon upstream are not implemented by this distribution.']
    elif profile != 'migration':
        raise ValueError('Unknown acceptance profile')
    return record


def validate(record, expected, directory):
    bindings = ['schema_version', 'cluster_uid', 'source_manifest_sha256', 'images_sha256']
    if expected.get('profile') == PUBLIC_PROFILE:
        bindings += ['profile', 'source_revision', 'package_sha256', 'excluded_checks', 'upstream_limitations']
        if set(record.get('site_checks', {})) != set(expected['site_checks']):
            raise ValueError('Public-release site check membership changed')
        if set(record.get('models', {})) != set(expected['models']):
            raise ValueError('Public-release model membership changed')
        if any(set(record['models'][name]) != set(checks) for name, checks in expected['models'].items()):
            raise ValueError('Public-release model check membership changed')
    for name in bindings:
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
            'missing_checks': missing, 'excluded_checks': expected.get('excluded_checks', {}), 'production_ready': False,
            'limitation': 'Checks recorded evidence and reviewer attestations; does not rerun tests or certify their contents.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['init', 'check'])
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--cluster-uid', required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    profile_arguments(parser)
    args = parser.parse_args()
    expected = from_arguments(args)
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
