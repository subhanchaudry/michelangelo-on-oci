"""Build and record the database transport image independently of the nine apps."""
import json
import shutil
from pathlib import Path


def build(core, config, tag, source_manifest_sha256):
    metadata = core.WORK/'router-image-build.json'
    context = core.WORK/'router-build'
    context.mkdir(exist_ok=True)
    shutil.copyfile(core.ROOT/'router-runtime.Dockerfile',context/'Dockerfile')
    image = config['registry']+':router-'+tag
    core.run(['docker','buildx','build','--platform','linux/amd64','--push',
              '--no-cache-filter','runtime-security','--metadata-file',metadata,
              '-f',context/'Dockerfile','-t',image,context])
    digest = json.loads(metadata.read_text())['containerimage.digest']
    import image_provenance
    images = image_provenance.pin({'mysql_router':{'image':image,'digest':digest,
        'pinned':config['registry']+'@'+digest}},core.run)
    item = images['mysql_router']
    item.update(installer_source_manifest_sha256=source_manifest_sha256,
                platform='linux/amd64',release=tag,security_gate='requires-runtime-scan')
    config['mysql_router_image'] = item['pinned']
    core.save(core.WORK/'support-images.json',images)
    return images
