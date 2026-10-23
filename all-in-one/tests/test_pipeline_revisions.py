import copy
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('pipeline_revisions', Path(__file__).resolve().parents[1]/'installer/pipeline_revisions.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class PipelineRevisionTests(unittest.TestCase):
    def stored_revision(self):
        value=module.build_revision(self.pipeline())
        value['metadata']['uid']='retained-server-identity'
        value['metadata']['annotations']['michelangelo/Immutable']='true'
        value['status']={'state':'REVISION_STATE_READY'}
        return value

    def test_archived_revision_is_read_without_recreation(self):
        stored=self.stored_revision();api=Mock(return_value={'revision':stored})
        result=module.ensure_revision(api,module.build_revision(self.pipeline()))
        self.assertEqual(result['metadata']['uid'],'retained-server-identity')
        self.assertEqual([call.args[:2] for call in api.call_args_list],[('Revision','Get')])
        module.require_ready_archive(result)

    def test_only_not_found_creates_a_new_revision(self):
        api=Mock(side_effect=[HTTPError('http://localhost',404,'Not found',{},io.BytesIO()),{}, {'revision':self.stored_revision()}])
        module.ensure_revision(api,module.build_revision(self.pipeline()))
        self.assertEqual([call.args[:2] for call in api.call_args_list],[('Revision','Get'),('Revision','Create'),('Revision','Get')])
        api=Mock(side_effect=HTTPError('http://localhost',403,'Forbidden',{},io.BytesIO()))
        with self.assertRaises(HTTPError):module.ensure_revision(api,module.build_revision(self.pipeline()))
        self.assertEqual(api.call_count,1)

    def test_create_conflict_requires_verified_existing_content(self):
        def calls(stored):return Mock(side_effect=[HTTPError('http://localhost',404,'Not found',{},io.BytesIO()),HTTPError('http://localhost',409,'Conflict',{},io.BytesIO()),{'revision':stored}])
        stored=self.stored_revision();self.assertEqual(module.ensure_revision(calls(stored),module.build_revision(self.pipeline()))['metadata']['uid'],stored['metadata']['uid'])
        stored['spec']['content']['spec']['manifest']['uniflowTar']='s3://unrelated/package'
        with self.assertRaises(RuntimeError):module.ensure_revision(calls(stored),module.build_revision(self.pipeline()))

    def test_archived_provenance_conflicts_are_not_adopted(self):
        for field,value in [('uid',''),('name','unrelated')]:
            stored=self.stored_revision();stored['metadata'][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):module.ensure_revision(Mock(return_value={'revision':stored}),module.build_revision(self.pipeline()))
        stored=self.stored_revision();stored['metadata']['annotations']['michelangelo.ai/source-pipeline-uid']='different-native-pipeline'
        with self.assertRaises(RuntimeError):module.ensure_revision(Mock(return_value={'revision':stored}),module.build_revision(self.pipeline()))

    def test_archived_building_or_mutable_revision_is_not_made_ready(self):
        for state,immutable in [(2,'true'),('REVISION_STATE_ERROR','true'),(3,'false')]:
            stored=self.stored_revision();stored['status']['state']=state;stored['metadata']['annotations']['michelangelo/Immutable']=immutable
            with self.subTest(state=state,immutable=immutable),self.assertRaises(RuntimeError):module.require_ready_archive(stored)
        stored=self.stored_revision();stored['status']['state']=3;module.require_ready_archive(stored)

    def pipeline(self):
        return {'metadata': {'name':'demo','namespace':'project','uid':'original-native-uid',
                             'annotations': {'michelangelo.ai/demo':'demo',
                                             'michelangelo.ai/workflow-package-sha256':'a'*64}},
                'spec': {'type':'PIPELINE_TYPE_TRAIN','manifest':{'uniflowTar':'s3://owned/package'}},
                'status': {'state':'PIPELINE_STATE_READY'}}

    def test_snapshot_changes_with_executable_definition_not_status(self):
        pipeline = self.pipeline(); original = copy.deepcopy(pipeline)
        first = module.build_revision(pipeline)
        self.assertEqual(pipeline, original)
        pipeline['status']['latestRevision']={'name':'previous','namespace':'project'}
        pipeline['metadata']['resourceVersion']='999'
        self.assertEqual(first['metadata']['name'], module.build_revision(pipeline)['metadata']['name'])
        pipeline['spec']['manifest']['uniflowTar']='s3://owned/new-package'
        self.assertNotEqual(first['metadata']['name'], module.build_revision(pipeline)['metadata']['name'])

    def test_source_provenance_is_installer_not_a_fabricated_git_commit(self):
        revision = module.build_revision(self.pipeline())
        self.assertEqual(revision['spec']['source'], module.SOURCE)
        self.assertNotIn('gitCommit', revision['spec'])
        self.assertEqual(revision['metadata']['annotations']['michelangelo.ai/source-pipeline-uid'], 'original-native-uid')

    def test_unowned_or_unready_pipeline_is_rejected(self):
        pipeline=self.pipeline();pipeline['metadata']['annotations']={}
        with self.assertRaisesRegex(RuntimeError,'installer-owned'):module.build_revision(pipeline)
        pipeline=self.pipeline();pipeline['status']['state']='PIPELINE_STATE_BUILDING'
        with self.assertRaisesRegex(RuntimeError,'ready'):module.build_revision(pipeline)

    def test_executable_snapshot_does_not_copy_database_row_identity(self):
        pipeline=self.pipeline()
        pipeline['metadata']['annotations']['michelangelo/MetadataStoragePrimaryKey']='historical-row'
        revision=module.build_revision(pipeline)
        self.assertNotIn('michelangelo/MetadataStoragePrimaryKey', revision['spec']['content']['metadata']['annotations'])
        self.assertEqual(pipeline['metadata']['annotations']['michelangelo/MetadataStoragePrimaryKey'],'historical-row')
        self.assertEqual(revision['metadata']['annotations']['michelangelo.ai/source-pipeline-uid'],'original-native-uid')
