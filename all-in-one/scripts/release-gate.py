#!/usr/bin/env python3
"""Check release evidence without changing either pilot or accepting exceptions."""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('site_acceptance',Path(__file__).with_name('site-acceptance.py'))
site=importlib.util.module_from_spec(spec);spec.loader.exec_module(site)
REQUIRED=[
 'publication_storage_failure','publication_interruption_retry','publication_cancellation',
 'publication_corrupt_artifact','ineligible_deployment_denied','maintenance_admission',
 'upgrade_interruption_recovery','upgrade_membership_history','matching_runtime_rollback',
 'old_pilot_inventory','consistent_app_and_orchestration_backup','isolated_database_restore',
 'destination_artifact_checksums','migration_reference_manifest','historical_studio_access',
 'migrated_model_execution','application_rollback_rehearsal','database_restore_rehearsal',
 'new_identity_directory_preserved','certificate_renewal','credential_rotation',
 'backup_monitoring','failure_alerts','budget_alerts','cold_start_and_image_prepull',
 'validation_archive_preserved',
]
BINDINGS=('cluster_uid','source_manifest_sha256','images_sha256')
MIGRATION_ONLY = {'old_pilot_inventory', 'migration_reference_manifest',
                  'historical_studio_access', 'migrated_model_execution'}
PUBLIC_REQUIRED = [name for name in REQUIRED if name not in MIGRATION_ONLY] + [
 'fresh_package_install', 'installer_rerun', 'full_independent_site_restore',
 'portable_and_source_checks', 'three_terraform_profiles', 'final_source_security_review',
 'publication_and_history_secret_scan', 'package_source_provenance',
 'authorization_regressions', 'complete_executable_inventory',
]

def pending():
 return {'status':'not-run','reviewer':'','completed_at':'','evidence':[]}

def template(acceptance,runtime_images):
 catalog=json.loads((ROOT/'installer/release/platform/serving/lifecycle/catalog.json').read_text())
 if len(runtime_images)!=9 or any(not isinstance(item,dict) or not item.get('runtime_digest') or item.get('digest')!=item.get('runtime_digest') for item in runtime_images.values()):
  raise ValueError('Release requires all nine executable image manifests, separate from attestations')
 public = acceptance.get('profile') == site.PUBLIC_PROFILE
 bindings = BINDINGS + (('profile','source_revision','package_sha256') if public else ())
 record = {'schema_version':2 if public else 1,'bindings':{k:acceptance[k] for k in bindings},
         'application_images':{name:item['runtime_digest'] for name,item in runtime_images.items()},
         'checks':{k:pending() for k in (PUBLIC_REQUIRED if public else REQUIRED)},'training':{m['demo']:pending() for m in catalog['models']},
         'security':{'status':'not-reviewed','reviewer':'','inventory_evidence':[],
                     'images':[],'findings':[]},'cutover':{'mode':'planned-pause','retention_days':7,'delete_old_pilot':False}}
 if public:
  record['cutover'] = {'mode':'none','delete_old_pilot':False}
 return record

def evidence(items,directory):
 if not isinstance(items,list) or not items:raise ValueError('Missing evidence')
 for item in items:
  relative=Path(item['path']);path=(directory/relative).resolve()
  if relative.is_absolute() or not path.is_relative_to(directory.resolve()):raise ValueError('Evidence escapes its archive')
  if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Evidence missing or changed: '+str(relative))

def reviewed(check,directory,now):
 if check.get('status')!='passed' or not str(check.get('reviewer','')).strip():raise ValueError('Check has not passed review')
 completed=datetime.datetime.fromisoformat(check['completed_at'].replace('Z','+00:00'))
 if completed.tzinfo is None or completed>now:raise ValueError('Invalid completion timestamp')
 evidence(check.get('evidence'),directory)

def validate(record,acceptance,expected_site,directory,acceptance_directory,runtime_images):
 expected=template(expected_site,runtime_images);blockers=[];now=datetime.datetime.now(datetime.timezone.utc)
 public = expected_site.get('profile') == site.PUBLIC_PROFILE
 if record.get('schema_version')!=expected['schema_version'] or record.get('bindings')!=expected['bindings']:raise ValueError('Qualification belongs to a different cluster or build')
 site_result=site.validate(acceptance,expected_site,acceptance_directory)
 blockers.extend('acceptance/'+x for x in site_result['missing_checks'])
 for group in ('checks','training'):
  for name in expected[group]:
   try:reviewed(record.get(group,{}).get(name,{}),directory,now)
   except (ValueError,KeyError,TypeError):blockers.append(group+'/'+name)
 security=record.get('security',{})
 if security.get('status')!='reviewed' or not security.get('reviewer'):blockers.append('security/review')
 inventory=[]
 try:
  evidence(security.get('inventory_evidence'),directory)
  inventory=json.loads((directory/security['inventory_evidence'][0]['path']).read_text())['images']
  if not inventory:raise ValueError('Empty inventory')
 except (ValueError,KeyError,TypeError):blockers.append('security/supporting-component-inventory')
 # Every inventory reference must identify the executable digest actually
 # scanned. Empty coverage cannot be interpreted as zero vulnerabilities.
 images=security.get('images',[])
 if not images:blockers.append('security/image-coverage')
 if record.get('application_images')!=expected['application_images']:blockers.append('security/application-image-binding')
 scanned={item.get('executable_digest') for item in images if item.get('status')=='passed'}
 for name,digest in expected['application_images'].items():
  if digest not in scanned:blockers.append('security/application-image/'+name)
 for component in inventory:
  reference=component.get('reference','');digest=component.get('resolved_executable_digest')
  matches=[item for item in images if item.get('reference')==reference and item.get('executable_digest')==digest]
  if not digest or not matches:blockers.append('security/supporting-image/'+reference)
 detected=[]
 for image in images:
  try:
   digest=image['executable_digest']
   if not isinstance(digest,str) or not digest.startswith('sha256:') or len(digest)!=71 or len(bytes.fromhex(digest[7:]))!=32:raise ValueError('Invalid digest')
   reviewed(image,directory,now)
   evidence(image.get('scan_evidence'),directory)
   scan=json.loads((directory/image['scan_evidence'][0]['path']).read_text())
   if not any(ref.endswith('@'+digest) for ref in scan.get('Metadata',{}).get('RepoDigests',[])):raise ValueError('Scan does not identify executable digest')
   if scan.get('ArtifactType')!='container_image' or not isinstance(scan.get('Results'),list):raise ValueError('Not a complete container scan')
   if public:
    # An unsupported/empty scanner result must never become zero findings.
    if not any(result.get('Packages') for result in scan['Results']):raise ValueError('Package inventory coverage missing')
    created=datetime.datetime.fromisoformat(scan['CreatedAt'].replace('Z','+00:00'))
    if created.tzinfo is None or created>now or now-created>datetime.timedelta(days=7):raise ValueError('Scan is not current')
   for result in scan['Results']:
    for vulnerability in result.get('Vulnerabilities',[]) or []:
     if vulnerability.get('Severity') in ('HIGH','CRITICAL'):
      detected.append((digest,vulnerability['VulnerabilityID'],vulnerability['Severity']))
  except (ValueError,KeyError,TypeError):blockers.append('security/image/'+str(image.get('name','unknown')))
 for finding in security.get('findings',[]):
  if finding.get('severity') not in ('HIGH','CRITICAL'):continue
  name='security/finding/'+str(finding.get('id','unknown'))
  try:
   if not finding.get('analysis') or not finding.get('owner'):raise ValueError('Missing disposition')
   evidence(finding.get('evidence'),directory)
   if finding.get('disposition') == 'fixed':continue
   if public:
    if finding.get('disposition')!='not-affected' or finding.get('exploitable') is not False or not finding.get('reviewer'):
     raise ValueError('Public release permits evidence-backed false positives, not risk exceptions')
    if not finding.get('executable_digests'):raise ValueError('False positive lacks digest binding')
    continue
   # Every remaining H/C finding needs explicit, expiring approval under this
   # pilot's release rules, including a scanner finding assessed not affected.
   if finding.get('disposition') not in ('not-affected','approved-exception') or finding.get('exploitable') is not False:raise ValueError('Unresolved exploitable finding')
   expiry=datetime.datetime.fromisoformat(finding['expires_at'].replace('Z','+00:00'))
   if expiry.tzinfo is None or expiry<=now or not finding.get('compensating_controls'):raise ValueError('Expired or incomplete exception')
   approval=finding.get('approval',{})
   if not approval.get('approver') or not approval.get('explicit') or not approval.get('reference'):raise ValueError('Explicit approval missing')
   evidence(approval.get('evidence'),directory)
  except (ValueError,KeyError,TypeError):blockers.append(name)
 for digest,identifier,severity in detected:
  dispositions=[finding for finding in security.get('findings',[]) if finding.get('id')==identifier and digest in finding.get('executable_digests',[]) and finding.get('severity')==severity and finding.get('disposition') in (('not-affected',) if public else ('not-affected','approved-exception'))]
  if not dispositions:blockers.append('security/undisposed-detection/'+identifier+'/'+digest)
 if record.get('cutover')!=expected['cutover']:blockers.append('cutover/profile-boundary')
 return {'cutover_allowed':not public and not blockers,'release_eligible':public and not blockers,
         'excluded_checks':site_result.get('excluded_checks',{}),'blockers':blockers,'production_ready':False,
         'limitation':'Validates reviewed evidence integrity, supplied inventory coverage and scan dispositions; does not execute tests, establish inventory completeness, approve exceptions, migrate data, or delete resources.'}

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('mode',choices=['init','check']);parser.add_argument('--record',type=Path,required=True)
 parser.add_argument('--acceptance',type=Path,required=True);parser.add_argument('--cluster-uid',required=True)
 parser.add_argument('--source-manifest',type=Path,required=True);parser.add_argument('--images',type=Path,required=True)
 site.profile_arguments(parser)
 args=parser.parse_args();expected=site.from_arguments(args)
 images=json.loads(args.images.read_text())
 if args.mode=='init':
  with args.record.open('x') as out:json.dump(template(expected,images),out,indent=2);out.write('\n')
  print('Pending release record created; cutover is not approved.');return 0
 report=validate(json.loads(args.record.read_text()),json.loads(args.acceptance.read_text()),expected,args.record.parent,args.acceptance.parent,images)
 print(json.dumps(report,indent=2));return 0 if report['release_eligible'] or report['cutover_allowed'] else 1

if __name__=='__main__':raise SystemExit(main())
