import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('pipeline_revisions', Path(__file__).resolve().parents[1]/'installer/pipeline_revisions.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class PipelineRevisionTests(unittest.TestCase):
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
