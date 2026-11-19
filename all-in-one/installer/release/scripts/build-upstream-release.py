#!/usr/bin/env python3
"""Build clean control-plane images with upstream release commands and Dockerfiles.

Requires an assembled source, Go, Node/Yarn, Helm, and a Docker Buildx builder.
Builds locally by default; --push explicitly publishes into the supplied registry.
No deployed image, tenancy secret, or local pilot backup is used as an image base.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--repository', required=True, help='Container registry/repository prefix')
    parser.add_argument('--tag', default='0.11.0-operator.3')
    parser.add_argument('--values', type=Path, required=True, help='Reviewed site values with existing Secret references')
    parser.add_argument('--push', action='store_true')
    parser.add_argument('--shared-repository', action='store_true', help='Use component-prefixed tags in one existing private repository')
    parser.add_argument('--examples-base', help='Optional upstream examples digest for the prepared demo-source application')
    args = parser.parse_args()
    source = args.source.resolve()
    output = source/'release-build'
    output.mkdir(exist_ok=True)
    env = dict(os.environ)
    def run(command, cwd=source, **kwargs):
        subprocess.run(command,cwd=cwd,check=True,**kwargs)
    # Fail before compiling if the supported container builder is unavailable.
    run(['docker','buildx','inspect','--bootstrap'])
    expected_node = json.loads((source/'javascript/package.json').read_text())['engines']['node']
    actual_node = subprocess.check_output(['node','--version'],text=True).strip().lstrip('v')
    if expected_node != actual_node:
        raise RuntimeError('Use upstream Node.js '+expected_node)
    run(['yarn','setup'],cwd=source/'javascript')
    run(['yarn','workspaces','run','build'],cwd=source/'javascript')
    env.update(GOOS='linux',GOARCH='amd64',CGO_ENABLED='0')
    components = {}
    def image_name(name):
        prefix=args.repository.rstrip('/')
        return prefix+':'+name+'-'+args.tag if args.shared_repository else prefix+'/'+name+':'+args.tag
    for name in ('apiserver','controllermgr','worker'):
        binary = output/name
        run(['go','build','-tags=kubernetes_protomessage_one_more_release','-trimpath','-o',str(binary),'./cmd/'+name],cwd=source/'go',env=env)
        image = image_name(name)
        run(['docker','buildx','build','--platform','linux/amd64','--push' if args.push else '--load','-f','docker/service.Dockerfile','--build-arg','BINARY_PATH=release-build/'+name,'--build-arg','CONFIG_PATH=go/cmd/'+name+'/config','--metadata-file',str(output/(name+'-image.json')),'-t',image,'.'])
        components[name] = {'image':image,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    ui = image_name('ui')
    run(['docker','buildx','build','--platform','linux/amd64','--push' if args.push else '--load','-f','docker/ui-prebuilt.Dockerfile','--metadata-file',str(output/'ui-image.json'),'-t',ui,'.'])
    components['ui'] = {'image':ui}
    if args.examples_base:
        if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}',args.examples_base):
            raise ValueError('Examples base must be digest pinned')
        if not (source/'demo-source-checksums.json').is_file():
            raise ValueError('Run prepare-demo-image-context.py before building the demo runtime')
        demo_image=image_name('examples')
        run(['docker','buildx','build','--platform','linux/amd64','--push' if args.push else '--load','-f','docker/demo-runtime.Dockerfile','--build-arg','EXAMPLES_BASE='+args.examples_base,'--metadata-file',str(output/'examples-image.json'),'-t',demo_image,'.'])
        components['examples']={'image':demo_image,'base':args.examples_base}
    run(['helm','dependency','build','helm/michelangelo'])
    run(['helm','lint','helm/michelangelo','-f',str(args.values.resolve())])
    run(['helm','package','helm/michelangelo','--destination',str(output)])
    for name in components:
        metadata=json.loads((output/(name+'-image.json')).read_text())
        components[name]['digest']=metadata.get('containerimage.digest')
    (output/'images.json').write_text(json.dumps(components,indent=2)+'\n')
    print('Built release artifacts at '+str(output)+'; runtime acceptance remains required.')

if __name__ == '__main__':
    main()
