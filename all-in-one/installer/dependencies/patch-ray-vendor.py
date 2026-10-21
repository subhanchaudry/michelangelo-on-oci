"""Replace Ray's separately imported aiohttp copy using a reviewed wheel hash."""
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
subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--no-deps',
                '--require-hashes', '--upgrade', '--target', str(vendor), '-r', str(lock)], check=True)
# pip --target replaces package code but retains the previous version metadata.
# Remove that superseded record only after the replacement installation succeeds.
shutil.rmtree(metadata[0])
subprocess.run([sys.executable, '-c',
    'import sys;sys.path.insert(0,sys.argv[1]);import aiohttp;'
    'assert aiohttp.__version__ == "3.14.4";'
    'assert aiohttp.__file__.startswith(sys.argv[1]+"/")', str(vendor)], check=True)
if [p.name for p in vendor.glob('aiohttp-*.dist-info')] != ['aiohttp-3.14.4.dist-info']:
    raise RuntimeError('Ray vendored dependency metadata is inconsistent')
