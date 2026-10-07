#!/usr/bin/env python3
"""Check a private upgrade render against its captured pilot baseline."""
import argparse
import json
from pathlib import Path
import yaml


def documents(path):
    return {(x['kind'], x['metadata'].get('namespace', 'ma-system'), x['metadata']['name']): x
            for x in yaml.safe_load_all(path.read_text()) if x}


def verify(before, rendered, images):
    old = documents(before/'helm-manifest.yaml')
    new = documents(rendered)
    assert not old.keys()-new.keys(), 'Previously managed resources omitted'
    live = {(x['kind'], x['metadata'].get('namespace','ma-system'), x['metadata']['name']): x
            for x in json.loads((before/'workloads.json').read_text())['items']}
    approved = {r['image'].rsplit(':',1)[0]+'@'+r['digest'] for r in images.values()}
    old_images = {c['image'] for x in live.values() if x['kind']=='Deployment'
                  for c in x['spec']['template']['spec'].get('containers',[])
                  + x['spec']['template']['spec'].get('initContainers',[])}
    for key, resource in new.items():
        kind, namespace, name = key
        if kind in ('Role','ClusterRole','RoleBinding','ClusterRoleBinding'):
            assert key in old, 'New permission resource requires separate review: '+name
            for field in ('rules','subjects','roleRef'):
                assert old[key].get(field)==resource.get(field), 'Permission expansion: '+name
        if kind=='Service':
            assert resource['spec'].get('type','ClusterIP')=='ClusterIP', 'Public service: '+name
            if key in old:
                assert resource['spec'].get('ports')==old[key]['spec'].get('ports'), 'Service routing changed: '+name
        assert kind not in ('Ingress','Gateway','HTTPRoute'), 'Unexpected public/routing change: '+name
        if kind!='Deployment': continue
        assert key in live, 'New application deployment requires review: '+name
        assert resource['spec'].get('replicas',1)==live[key]['spec'].get('replicas',1), 'Replica count changed: '+name
        pod=resource['spec']['template']['spec']
        previous=live[key]['spec']['template']['spec']
        assert pod.get('serviceAccountName')==previous.get('serviceAccountName'), 'Runtime identity changed: '+name
        assert pod.get('imagePullSecrets')==previous.get('imagePullSecrets'), 'Registry access changed: '+name
        for c in pod.get('containers',[])+pod.get('initContainers',[]):
            assert c['image'] in approved | old_images, 'Unreviewed image: '+name
            assert not any('/oci-bin/' in v for v in c.get('command',[])), 'Old layered binary entrypoint: '+name
            assert c['name']!='build-oci-s3-region-patch', 'Startup source build reintroduced'
            previous_container=next((x for x in previous.get('containers',[])+previous.get('initContainers',[]) if x['name']==c['name']),None)
            if previous_container:
                assert (c.get('resources') or {})==(previous_container.get('resources') or {}), 'Resource limits changed: '+name+'/'+c['name']
    ui=next(x for k,x in new.items() if k[0]=='Deployment' and k[2]=='michelangelo-ui')
    assert any(c['name']=='pilot-login' for c in ui['spec']['template']['spec']['containers']), 'Private login sidecar missing'
    old_ui=next(x for k,x in live.items() if k[0]=='Deployment' and k[2]=='michelangelo-ui')
    login=next(c for c in ui['spec']['template']['spec']['containers'] if c['name']=='pilot-login')
    old_login=next(c for c in old_ui['spec']['template']['spec']['containers'] if c['name']=='pilot-login')
    for field in ('command','args','env','envFrom'):
        assert login.get(field)==old_login.get(field), 'Private login configuration changed: '+field
    checkpoint=json.loads((before/'checkpoint.json').read_text())
    assert checkpoint['idle'], 'Pilot was not idle at checkpoint'
    backup=json.loads((before/'mysql-backup.json').read_text())['data']
    assert backup['lifecycle-state']=='ACTIVE', 'Database recovery checkpoint unavailable'
    return {'managed_resources_preserved':True,'permissions_preserved':True,'private_services':True,'capacity_preserved':True,'database_backup_active':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before',type=Path,required=True)
    p.add_argument('--rendered',type=Path,required=True)
    p.add_argument('--images',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); result=verify(a.before,a.rendered,json.loads(a.images.read_text()))
    a.output.write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result))


if __name__=='__main__': main()
