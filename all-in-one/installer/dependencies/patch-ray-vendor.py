"""Refresh Ray's separate HTTP dependency closure and exercise its server imports."""
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
lock = Path(sys.argv[2]).resolve()
vendor = root/'ray/_private/runtime_env/agent/thirdparty_files'
if not vendor.is_dir():
    raise RuntimeError('Reviewed Ray vendored import directory is missing')
metadata = list(vendor.glob('aiohttp-*.dist-info'))
if len(metadata) != 1 or metadata[0].name != 'aiohttp-3.14.1.dist-info':
    raise RuntimeError('Ray vendored aiohttp differs from the reviewed input')
# pip --target leaves superseded distribution metadata behind.
packages = {line.split(' @ ')[0].lower().replace('-', '_') for line in lock.read_text().splitlines()}
old_metadata = [p for p in vendor.glob('*.dist-info')
                if p.name.removesuffix('.dist-info').rsplit('-', 1)[0].lower().replace('-', '_') in packages]
subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--no-deps',
                '--require-hashes', '--upgrade', '--target', str(vendor), '-r', str(lock)], check=True)
# pip --target replaces package code but retains the previous version metadata.
# Remove that superseded record only after the replacement installation succeeds.
for path in old_metadata:
    package = path.name.removesuffix('.dist-info').rsplit('-', 1)[0]
    if any(p != path for p in vendor.glob(package+'-*.dist-info')):
        shutil.rmtree(path)
subprocess.run([sys.executable, '-c',
    'import sys;sys.path.insert(0,sys.argv[1]);import aiohttp;'
    'assert aiohttp.__version__ == "3.14.4";'
    'assert aiohttp.__file__.startswith(sys.argv[1]+"/");'
    'from aiohttp import web;from yarl import URL,query_to_pairs;'
    'app=web.Application();assert URL("http://localhost").with_query(a=1).query["a"] == "1"', str(vendor)], check=True)
if [p.name for p in vendor.glob('aiohttp-*.dist-info')] != ['aiohttp-3.14.4.dist-info']:
    raise RuntimeError('Ray vendored dependency metadata is inconsistent')
# These images execute Python Ray tasks and the Python inference backend.
# Do not ship the unused Java worker and its separate dependency closure.
jars = root / 'ray/jars'
if jars.is_dir():
    shutil.rmtree(jars)
