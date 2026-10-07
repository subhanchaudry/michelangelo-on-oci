"""Versioned artifact IO restricted to this workflow's project prefix."""
import hashlib
import json
import os
from urllib.parse import urlsplit
import boto3


def client():
    return boto3.client('s3', endpoint_url=os.environ['AWS_ENDPOINT_URL'], region_name=os.environ['AWS_REGION'])


def project_prefix():
    namespace = os.environ.get('MA_NAMESPACE') or os.environ['REGISTRY_NAMESPACE']
    if '/' in namespace or not namespace:
        raise ValueError('Invalid artifact namespace')
    return 'studio-demos/lifecycle/' + namespace + '/'


def get_bytes(uri, checksum, max_bytes=2*1024**3):
    parsed = urlsplit(uri)
    if parsed.scheme != 's3' or parsed.netloc != os.environ['AWS_S3_BUCKET'] or parsed.query or parsed.fragment or not parsed.path.lstrip('/').startswith(project_prefix()):
        raise ValueError('Artifact is outside this project lifecycle prefix')
    if not checksum or len(checksum) != 64:
        raise ValueError('An immutable artifact checksum is required')
    response = client().get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip('/'))
    body = response['Body']
    try:
        if not 0 < response.get('ContentLength',0) <= max_bytes:
            raise ValueError('Artifact exceeds the permitted size')
        data = body.read(max_bytes+1)
    finally:
        body.close()
    if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Corrupt or incompatible artifact checksum')
    return data


def put_bytes(category, data, suffix):
    checksum = hashlib.sha256(data).hexdigest()
    key = project_prefix() + category + '/' + checksum + suffix
    client().put_object(Bucket=os.environ['AWS_S3_BUCKET'], Key=key, Body=data,
                       Metadata={'sha256': checksum})
    return 's3://' + os.environ['AWS_S3_BUCKET'] + '/' + key, checksum


def put_json(category, value):
    return put_bytes(category,json.dumps(value,sort_keys=True,allow_nan=False).encode(),'.json')


def publish_log(source_run, text):
    import re
    if not re.fullmatch('[a-z0-9-]+', source_run):
        raise ValueError('Invalid source run name')
    payload=text.encode()
    if len(payload)>8*1024**2:
        payload=payload[-8*1024**2:]
    uri,checksum=put_bytes('logs',payload,'.log')
    project=os.environ.get('MA_NAMESPACE') or os.environ['REGISTRY_NAMESPACE']
    record={'source_run':source_run,'project':project,'uri':uri,'sha256':checksum}
    client().put_object(Bucket=os.environ['AWS_S3_BUCKET'],Key=project_prefix()+'logs/index/'+source_run+'.json',Body=json.dumps(record).encode())
    return uri,checksum
