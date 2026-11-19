#!/usr/bin/env python3
"""Package explicit source inputs, preserve immutable releases, and verify the core."""
import argparse
import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify_installer_imports(root, files):
    """Reject an allowlist that omits a locally imported installer module."""
    for name, data in files.items():
        if not (name.startswith('installer/') and name.count('/') == 1 and name.endswith('.py')):
            continue
        for node in ast.walk(ast.parse(data, filename=name)):
            if isinstance(node, ast.Import):
                modules = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.split('.')[0]]
            else:
                continue
            for module in modules:
                dependency = 'installer/' + module + '.py'
                if (root / dependency).is_file() and dependency not in files:
                    raise ValueError('Missing installer module in package: ' + dependency + ' (imported by ' + name + ')')


def documentation(repository):
    """Keep the README's local diagram and guide links usable in every ZIP."""
    names = ['README.md', 'LICENSE', 'LICENSES/Michelangelo-Apache-2.0.txt',
             'THIRD-PARTY-NOTICES.md', 'SECURITY.md', 'docs/architecture.md',
             'docs/architecture.svg', 'docs/architecture.png', 'docs/installation.md',
             'docs/operations.md', 'docs/security.md', 'docs/release-notes.md',
             'DEPENDENCIES.json']
    files = {}
    repository = repository.resolve()
    for name in names:
        path = repository / name
        if path.is_symlink() or not path.resolve().is_relative_to(repository):
            raise ValueError('Unsafe documentation path: ' + name)
        if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(repository)):
            raise ValueError('Symlinked documentation directory: ' + name)
        files[name] = path.read_bytes()
    return files


def payload(root=ROOT):
    root = root.resolve()
    names = json.loads((root / 'PACKAGE-FILES.json').read_text())
    if len(names) != len(set(names)):
        raise ValueError('Duplicate package path')
    files = {}
    for name in names:
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe package path: ' + name)
        if any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError('Symlinked package directory: ' + name)
        files[name] = path.read_bytes()
    verify_installer_imports(root, files)
    # The inherited verifier checks every nested payload hash, its inventory,
    # relative documentation links and the absence of inherited site acceptance.
    verifier = root / 'installer/release/scripts/verify-review-bundle.py'
    spec = importlib.util.spec_from_file_location('release_verifier', verifier)
    module = importlib.util.module_from_spec(spec)
    # Importing the verifier must not add bytecode to the immutable payload.
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    module.verify(root / 'installer/release')
    files.update(documentation(root.parent))
    for name, data in files.items():
        if any(b'-----BEGIN ' + kind + b'PRIVATE KEY-----' in data for kind in (b'', b'RSA ', b'EC ', b'OPENSSH ', b'ENCRYPTED ')):
            raise ValueError('Private key in package input: ' + name)
    metadata = json.loads(files['CONSOLIDATION.json'])
    manifest = {'format': 'Michelangelo OCI consolidated stack v1', 'release': metadata['release'], 'profile': 'all-in-one',
                'production_ready': False, 'files': {n: hashlib.sha256(v).hexdigest() for n, v in sorted(files.items())}}
    files['SOURCE-MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    return files


def foundation_payload(profile):
    root = ROOT.parent / profile
    names = ['iam.tf', 'locals.tf', 'network.tf', 'oke.tf', 'outputs.tf',
             'platform.tf', 'variables.tf', 'versions.tf', '.terraform.lock.hcl']
    names += ['schema.yaml'] if profile == 'resource-manager' else ['terraform.tfvars.example']
    files = {}
    for name in names:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unsafe foundation path: ' + name)
        files[name] = path.read_bytes()
    files.update(documentation(ROOT.parent))
    files['SOURCE-MANIFEST.json'] = (json.dumps({'profile': profile, 'release': json.loads((ROOT/'CONSOLIDATION.json').read_text())['release'], 'files': {
        n: hashlib.sha256(v).hexdigest() for n, v in sorted(files.items())}}, indent=2) + '\n').encode()
    return files


def check_publication(files):
    import runpy
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        checker = runpy.run_path(str(ROOT / 'scripts/check-publication.py'))
        checker['check'](files)
    finally:
        sys.dont_write_bytecode = previous


def package(destination, root=ROOT, profile='all-in-one'):
    destination = Path(destination)
    checksum_path = destination.with_suffix(destination.suffix + '.sha256')
    if destination.exists() or checksum_path.exists():
        raise ValueError('Release output exists; use a new versioned path')
    files = payload(root) if profile == 'all-in-one' else foundation_payload(profile)
    check_publication(files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            archive.writestr(entry, data)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    with checksum_path.open('x') as stream:
        stream.write(digest + '  ' + destination.name + '\n')
    return {'zip': str(destination), 'sha256': digest, 'payload_files': len(files) - 1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('all-in-one', 'terraform', 'resource-manager'), default='all-in-one')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output.resolve(), profile=args.profile), indent=2))
