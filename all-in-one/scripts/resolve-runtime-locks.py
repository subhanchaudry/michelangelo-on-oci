#!/usr/bin/env python3
"""Resolve reviewed inputs inside Linux/amd64 Python 3.12 into hash locks.

Run only on the build host, with a reconstructed SDK and a disposable Python
container. The local SDK is built separately from the same source manifest.
"""
import argparse
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--only', choices=('builder', 'delivery', 'identity', 'serving', 'cpu'))
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('Resolve in the reviewed Linux/amd64 Python 3.12 image')
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ([args.only] if args.only else ('builder', 'delivery', 'identity', 'serving', 'cpu')):
        report = args.output / (name + '-pip-report.json')
        command = [sys.executable, '-m', 'pip', 'install', '--dry-run', '--ignore-installed',
                   '--only-binary=:all:', '--report', str(report), '-r', str(args.inputs / (name + '.in'))]
        if name == 'cpu':
            command.append(str(args.sdk.resolve()) + '[trainer-xgboost,minio]')
        subprocess.run(command, check=True)
        rows = json.loads(report.read_text())['install']
        lines, versions = [], {}
        for row in rows:
            package = row['metadata']['name']
            if package.lower() == 'michelangelo':
                continue
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', package):
                raise RuntimeError('Invalid resolved package name')
            versions[package.lower().replace('_', '-')] = row['metadata']['version']
            download = row['download_info']
            uri = download['url']
            parsed = urlsplit(uri)
            if parsed.scheme != 'https' or parsed.hostname not in ('files.pythonhosted.org', 'download.pytorch.org', 'download-r2.pytorch.org') or not parsed.path.endswith('.whl'):
                raise RuntimeError('Expected an official hashable wheel: ' + package)
            digest = download['archive_info']['hashes']['sha256']
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise RuntimeError('Invalid wheel checksum')
            lines.append(f'{package} @ {uri} --hash=sha256:{digest}')
        if 'pyspark' in versions:
            raise RuntimeError('CPU Ray image unexpectedly selected the optional Spark plugin')
        if name in ('cpu', 'serving'):
            for package, version in {'torch': '2.6.0+cpu', 'ray': '2.56.1', 'transformers': '5.10.0', 'numpy': '1.26.4'}.items():
                if versions.get(package) != version:
                    raise RuntimeError('Unexpected model dependency: ' + package)
        (args.output / (name + '-requirements.lock')).write_text('\n'.join(sorted(lines, key=str.lower)) + '\n')
        (args.output / (name + '-versions.json')).write_text(json.dumps(versions, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'lock': name, 'packages': len(versions)}), flush=True)


if __name__ == '__main__':
    main()
