"""Reconstruct the selected upstream release without modifying inherited inputs."""
import hashlib
import json
from pathlib import Path
import time

SELECTED=Path(__file__).parent/'sources/v0.13.0/manifest.json'

def manifest():
    value=json.loads(SELECTED.read_text())
    patch=SELECTED.parent/value['patch']['file']
    if patch.resolve().parent != SELECTED.parent.resolve() or patch.is_symlink():
        raise RuntimeError('Invalid reviewed source patch path')
    if hashlib.sha256(patch.read_bytes()).hexdigest()!=value['patch']['sha256']:
        raise RuntimeError('Reviewed source patch checksum mismatch')
    return value,patch

def assemble(core):
    value,patch=manifest()
    package_manifest=core.ROOT.parent/'SOURCE-MANIFEST.json'
    if not package_manifest.is_file():
        raise RuntimeError('Source assembly requires the packaged manifest')
    package_digest=hashlib.sha256(package_manifest.read_bytes()).hexdigest()
    identity={'commit':value['upstream_commit'],'patch':value['patch']['sha256'],'installer_source_manifest_sha256':package_digest}
    destination=core.WORK/('source-'+value['upstream_tag']+'-'+package_digest[:16])
    marker=destination/'.oci-release.json'
    if destination.exists():
        if not marker.is_file() or json.loads(marker.read_text())!=identity:
            raise RuntimeError('Existing source is not the selected reviewed release')
        if core.run(['git','rev-parse','HEAD'],cwd=destination,capture=True).strip()!=identity['commit']:
            raise RuntimeError('Selected upstream source identity changed')
        return destination
    staging=destination.with_name(destination.name+'.assembling')
    if staging.exists():
        # Preserve interrupted reconstruction for diagnosis; never reset a tree.
        staging.rename(staging.with_name(staging.name+'.interrupted-'+str(time.time_ns())))
    core.run(['git','clone','--branch',value['upstream_tag'],'--single-branch','--depth','1',value['repository'],staging])
    if core.run(['git','rev-parse','HEAD'],cwd=staging,capture=True).strip()!=identity['commit']:
        raise RuntimeError('Upstream tag differs from the pinned reviewed commit')
    core.run(['git','apply','--check',patch.resolve()],cwd=staging)
    core.run(['git','apply',patch.resolve()],cwd=staging)
    core.run(['git','diff','--check'],cwd=staging)
    core.save(staging/'.oci-release.json',identity)
    staging.rename(destination)
    return destination
