"""Reconcile Michelangelo model-list.json into a pod-local Triton repository.

The controller supplies the immutable package checksum in each model config.
Storage credentials are injected into this container only. Model loading is
explicit; successful download alone is never reported as model readiness.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from package import PackageError, unpack_model

LOG = logging.getLogger('model-delivery')


class Delivery:
    def __init__(self, storage, bucket, prefix, repository, triton='http://127.0.0.1:8000'):
        self.storage = storage
        self.bucket = bucket
        self.prefix = prefix.rstrip('/') + '/'
        self.repository = Path(repository)
        self.triton = triton
        self.loaded = {}

    def request(self, path, method='GET'):
        with urlopen(Request(self.triton + path, data=b'{}' if method == 'POST' else None,
                             method=method, headers={'Content-Type': 'application/json'}), timeout=15) as response:
            if response.status != 200:
                raise RuntimeError('Triton rejected ' + path)

    def validate(self, entries):
        if not isinstance(entries, list) or len(entries) > 2:
            raise PackageError('Expected at most two revisions during operator rollout')
        desired = {}
        for entry in entries:
            name = entry['name']
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', name) or name in desired:
                raise PackageError('Invalid or duplicate Triton model name')
            uri = urlsplit(entry['storage_path'])
            checksum = entry.get('sha256', '')
            if (uri.scheme != 's3' or uri.netloc != self.bucket or uri.query or uri.fragment
                    or not uri.path.lstrip('/').startswith(self.prefix)
                    or not re.fullmatch(r'[0-9a-f]{64}', checksum)):
                raise PackageError('Model package is outside the permitted artifact scope or lacks checksum')
            desired[name] = (uri.path.lstrip('/'), checksum)
        return desired

    def reconcile(self, entries):
        desired = self.validate(entries)
        self.repository.mkdir(parents=True, exist_ok=True)
        # New models are verified and loaded before any removed revision is unloaded.
        for name, (key, checksum) in desired.items():
            if self.loaded.get(name) == checksum:
                self.request('/v2/models/' + name + '/ready')
                continue
            if name in self.loaded:
                raise PackageError('A model revision must be immutable; use a new revision name')
            destination = self.repository / name
            if destination.exists():
                # Restart may leave a partial, unconfirmed model. Unload before replacement.
                self.request('/v2/repository/models/' + name + '/unload', 'POST')
                shutil.rmtree(destination)
            with tempfile.TemporaryDirectory(dir=self.repository) as td:
                archive = Path(td) / 'package.tar'
                response = self.storage.get_object(Bucket=self.bucket, Key=key)
                size = response.get('ContentLength', 0)
                if size <= 0 or size > 2 * 1024**3:
                    response['Body'].close()
                    raise PackageError('Invalid model package download size')
                body = response['Body']
                try:
                    with archive.open('wb') as out:
                        count = 0
                        for block in iter(lambda: body.read(1024 * 1024), b''):
                            count += len(block)
                            if count > size:
                                raise PackageError('Model download exceeds declared size')
                            out.write(block)
                    if count != size:
                        raise PackageError('Truncated model download')
                finally:
                    body.close()
                unpack_model(archive, destination, checksum)
            self.request('/v2/repository/models/' + name + '/load', 'POST')
            self.request('/v2/models/' + name + '/ready')
            self.loaded[name] = checksum
            LOG.info('Model ready: %s sha256=%s', name, checksum)
        for name in list(self.loaded):
            if name not in desired:
                self.request('/v2/repository/models/' + name + '/unload', 'POST')
                shutil.rmtree(self.repository / name)
                del self.loaded[name]
                LOG.info('Model unloaded: %s', name)


def main():
    import boto3
    from botocore.config import Config
    logging.basicConfig(level=logging.INFO)
    storage = boto3.client('s3', endpoint_url=os.environ['AWS_ENDPOINT_URL'],
                           region_name=os.environ['AWS_REGION'],
                           config=Config(connect_timeout=10, read_timeout=30, retries={'max_attempts': 3}))
    delivery = Delivery(storage, os.environ['AWS_S3_BUCKET'], os.environ['MODEL_ARTIFACT_PREFIX'], '/mnt/models')
    while True:
        try:
            path = Path('/etc/michelangelo/models/model-list.json')
            delivery.reconcile(json.loads(path.read_text()) if path.exists() else [])
        except Exception:
            LOG.exception('Model reconciliation failed; no readiness is asserted')
        time.sleep(5)

if __name__ == '__main__':
    main()
