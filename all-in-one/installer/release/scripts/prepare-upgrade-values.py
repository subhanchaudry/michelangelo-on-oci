#!/usr/bin/env python3
"""Produce private upgrade values only from complete immutable build results."""
import argparse
import json
import os
from pathlib import Path
import re

DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
IMAGE = re.compile(r'^[^\s@]+@sha256:[0-9a-f]{64}$')

def prepare(previous, built, triton):
    if not IMAGE.fullmatch(triton):
        raise ValueError('Triton image must be digest pinned')
    values = json.loads(json.dumps(previous))
    for component in ('apiserver','controllermgr','worker','ui'):
        record = built[component]
        digest = record.get('digest') or ''
        if not DIGEST.fullmatch(digest) or digest == 'sha256:'+'0'*64:
            raise ValueError('Missing immutable build digest for '+component)
        tagged = record['image']
        repository = tagged.rsplit('@',1)[0]
        if ':' in repository.rsplit('/',1)[-1]: repository = repository.rsplit(':',1)[0]
        values['images'][component] = {'repository':repository+'@'+digest,'tag':''}
        if component != 'ui':
            values[component]['command'] = ['/app']
    values.setdefault('inferenceServer',{}).setdefault('triton',{})['defaultImage'] = triton
    if not values.get('ui',{}).get('basicAuthSecret'):
        raise ValueError('Private pilot login Secret must be preserved')
    for component in ('apiserver','envoy','ui'):
        settings=values.get(component,{})
        if settings.get('service',{}).get('type','ClusterIP') != 'ClusterIP' or settings.get('ingress',{}).get('enabled',False):
            raise ValueError('This pilot upgrade must remain private: '+component)
    return values

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous',type=Path,required=True)
    p.add_argument('--images',type=Path,required=True)
    p.add_argument('--triton-image',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    values=prepare(json.loads(a.previous.read_text()),json.loads(a.images.read_text()),a.triton_image)
    os.umask(0o077)
    with a.output.open('x') as stream: stream.write(json.dumps(values,indent=2)+'\n')
    print('Private upgrade values prepared at '+str(a.output))

if __name__=='__main__': main()
