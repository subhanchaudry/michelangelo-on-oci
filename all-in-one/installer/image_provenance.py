"""Pin executable linux/amd64 manifests; retain original attestation indexes."""
import json
import re


def runtime_digest(document, current):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',current):
        raise ValueError('Image reference must identify an immutable digest')
    if 'manifests' not in document:
        if document.get('mediaType') not in ('application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'):
            raise ValueError('Unsupported image manifest')
        return current
    matches = [m['digest'] for m in document['manifests'] if m.get('platform', {}).get('os') == 'linux' and m.get('platform', {}).get('architecture') == 'amd64']
    if len(matches) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}',matches[0]):
        raise ValueError('Expected one executable linux/amd64 image')
    return matches[0]


def pin(images, run):
    for item in images.values():
        index = item['digest']; repository = item['pinned'].split('@')[0]
        raw = run(['docker','buildx','imagetools','inspect','--raw',repository+'@'+index],capture=True)
        digest = runtime_digest(json.loads(raw), index)
        item.update(index_digest=index, runtime_digest=digest, digest=digest, pinned=repository+'@'+digest)
    return images
