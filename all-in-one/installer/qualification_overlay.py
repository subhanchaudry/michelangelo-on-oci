"""Qualification changes are overlays; the inherited release stays immutable."""
from pathlib import Path
import json
from identity_overlay import replace

ROOT = Path(__file__).parent

def prepare_source(source):
    source = Path(source)
    for path in (ROOT / 'qualification').glob('*.go'):
        (source/'go/api/handler'/path.name).write_bytes(path.read_bytes())
    catalog = json.loads((ROOT/'release/platform/serving/lifecycle/catalog.json').read_text())
    projects = {}
    for model in catalog['models']:
        pipelines = projects.setdefault(model['project'], {})
        for name in [model['demo'], *[p['name'] for p in model['pipelines']]]:
            pipelines[name] = model['demo']
    lines = ['package handler', 'var runnerPipelines = map[string]map[string]string{']
    for project, pipelines in sorted(projects.items()):
        lines.append(json.dumps(project)+': {')
        lines.extend(json.dumps(name)+': '+json.dumps(demo)+',' for name,demo in sorted(pipelines.items()))
        lines.append('},')
    (source/'go/api/handler/runner_catalog.go').write_text('\n'.join(lines+['}'])+'\n')
    prepare_runner_guards(source)
    prepare_tests(source)
    prepare_controller_tests(source)
    target = source/'go/api/handler/handler.go'
    replace(target, 'if err := auth.StampIdentity(ctx, obj, true); err != nil { return err }',
        'if err := auth.StampIdentity(ctx, obj, true); err != nil { return err }\n    if err := handler.validateReleaseMutation(ctx, obj, nil); err != nil { return err }',
        'handler.validateReleaseMutation(ctx, obj, nil)')
    replace(target, 'if err := auth.StampIdentity(ctx, obj, false); err != nil { return err }',
        'if err := auth.StampIdentity(ctx, obj, false); err != nil { return err }\n    if err := handler.validateReleaseMutation(ctx, obj, original); err != nil { return err }',
        'handler.validateReleaseMutation(ctx, obj, original)')


def prepare_demo(source):
    directory = Path(source)/'demo-source/oci_lifecycle'
    # Reset only the reviewed lifecycle files this overlay owns before applying.
    for name in ('registry.py','runner.py'):
        (directory/name).write_bytes((ROOT/'release/platform/serving/lifecycle'/name).read_bytes())
    registry = directory/'registry.py'
    replace(registry, "annotations = {'michelangelo.ai/package-sha256': package_checksum,", """# Independently hash the persisted evaluation; the finalizer verifies it again.
    import storage
    from urllib.parse import urlsplit
    parsed = urlsplit(evaluation_uri)
    if parsed.scheme != 's3' or parsed.netloc != os.environ['AWS_S3_BUCKET'] or not parsed.path.lstrip('/').startswith(storage.project_prefix()):
        raise ValueError('Evaluation is outside this project')
    response = storage.client().get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip('/'))
    with response['Body'] as body:
        evaluation = body.read(16*1024**2+1)
    if not evaluation or len(evaluation)>16*1024**2:
        raise ValueError('Invalid evaluation size')
    annotations = {'michelangelo.ai/publication-state': 'pending',
                   'michelangelo.ai/evaluation-sha256': hashlib.sha256(evaluation).hexdigest(),
                   'michelangelo.ai/package-sha256': package_checksum,""")
    replace(registry, "'michelangelo.ai/eligible': 'true'", "'michelangelo.ai/eligible': 'false'")
    replace(registry, "Eligible candidate; operator deployment required", "Pending source and artifact verification; operator deployment required")
    replace(registry, "return {'eligible': True, 'model_registered': True,", "return {'eligible': False, 'publication_state': 'pending', 'model_registered': True,")
    replace(registry, "'evaluation_report': report_name, 'project': namespace}", """'evaluation_report': report_name, 'project': namespace,
            'candidate_binding': __import__('publication').candidate_binding(
                __import__('google.protobuf.json_format', fromlist=['MessageToDict']).MessageToDict(registered))}""")
    replace(registry, "    if not model.spec.deployable_artifact_uri:", """    if model.metadata.annotations.get('michelangelo.ai/publication-state') != 'eligible':
        raise ValueError('Model publication has not been finalized')
    source = model.spec.source_pipeline_run
    if not source.name or source.namespace != namespace:
        raise ValueError('Model source run is missing or outside this project')
    run = api_client().PipelineRunService.get_pipeline_run(namespace, source.name)
    if run.status.state != 3:
        raise ValueError('Model source run did not succeed')
    if not model.spec.deployable_artifact_uri:""")
    (directory/'publication.py').write_bytes((ROOT/'qualification/publication.py').read_bytes())
    (directory.parent/'oci_demos/training_workflows.py').write_bytes((ROOT/'qualification/training_workflows.py').read_bytes())


def prepare_tests(source):
    """Port storage tests to verified identity without weakening production auth."""
    directory=Path(source)/'go/api/handler'
    path=directory/'handler_test.go'
    text=path.read_text()
    if 'func setupK8sWithoutReleaseGate' not in text:
        text=text.replace('"context"','"context"\n corev1 "k8s.io/api/core/v1"',1)
        text=text.replace('func setupK8s()', 'func setupK8sWithoutReleaseGate()',1)
        text+='''
func setupK8s() (ctrlRTClient.Client, error) {
 client, err := setupK8sWithoutReleaseGate()
 if err != nil { return nil, err }
 gate := &corev1.ConfigMap{ObjectMeta:metav1.ObjectMeta{Name:"ma-release-control",Namespace:"ma-system"},Data:map[string]string{"maintenance":"false"}}
 if err := client.Create(context.Background(),gate); err != nil { return nil,err }
 return client,nil
}
'''
        path.write_text(text)
    if 'func qualifiedTestContext()' not in text:
        text=text.replace('"context"','"context"\n "github.com/michelangelo-ai/michelangelo/go/auth"',1)
        text=text.replace('context.Background()', 'qualifiedTestContext()')
        text+='\nfunc qualifiedTestContext() context.Context { return auth.WithPrincipal(context.Background(), auth.Principal{ID:"test-operator", Subject:"test-operator", Email:"test@example.com", Role:"operator", Enabled:true}) }\n'
        text=text.replace('assert.Equal(t, "test", updatedJob.Spec.User.Name)', 'assert.Equal(t, "test@example.com", updatedJob.Spec.User.Name)')
        # User attribution is immutable; exercise a real mutable spec field instead.
        text=text.replace('job.Spec.User = &v2pb.UserInfo{', 'job.Spec.JobId = "updated-job-id"\n job.Spec.User = &v2pb.UserInfo{')
        path.write_text(text)
    text=path.read_text()
    if 'func mockOriginalForQualifiedUpdate' not in text:
        text=text.replace('assert.Equal(t, "test-user-dryrun", updatedJob.Spec.User.Name)', 'assert.Equal(t, "test@example.com", updatedJob.Spec.User.Name)')
        text=text.replace('assert.Nil(t, unchangedJob.Spec.User, "Job should remain unchanged after failed dry run")', 'assert.Equal(t, "test@example.com", unchangedJob.Spec.User.Name, "Verified ownership survives failed dry run")')
        text=text.replace('// Perform dry run update - should succeed without calling metadataHandler.Update', 'mockOriginalForQualifiedUpdate(mockMetadataStorage, testJob)\n\t\t// Perform dry run update - should succeed without calling metadataHandler.Update')
        text=text.replace('// Stand in for the MySQL backend: assign', 'mockOriginalForQualifiedUpdate(mockMetadataStorage, testJob)\n\t// Stand in for the MySQL backend: assign')
        # Existing metadata-only test checks response serialization. The original
        # must now be read for provenance validation before the update is allowed.
        start=text.index('func TestUpdateEchoesCallerSpecForMetadataOnlyObjects')
        before, tail=text[:start],text[start:]
        tail=tail.replace('\n\tmockMetadataStorage.EXPECT().', '\n\tmockOriginalForQualifiedUpdate(mockMetadataStorage, incoming)\n\tmockMetadataStorage.EXPECT().',1)
        text=before+tail
        text+='''
func mockOriginalForQualifiedUpdate(store *storagemocks.MockMetadataStorage, blobs *storagemocks.MockBlobStorage, original runtime.Object) {
 meta := original.(metav1.Object)
 store.EXPECT().GetByName(gomock.Any(), meta.GetNamespace(), meta.GetName(), gomock.Any()).Times(1).DoAndReturn(func(_ context.Context, _, _ string, target runtime.Object) error {
  reflect.ValueOf(target).Elem().Set(reflect.ValueOf(original.DeepCopyObject()).Elem())
  return nil
 })
 blobs.EXPECT().MergeWithExternalBlob(gomock.Any(), gomock.Any()).Times(1).Return(nil)
}
'''
        text=text.replace('mockOriginalForQualifiedUpdate(mockMetadataStorage, ', 'mockOriginalForQualifiedUpdate(mockMetadataStorage, mockBlobStorage, ')
        path.write_text(text)
    path=directory/'builder_test.go';text=path.read_text()
    if '"github.com/michelangelo-ai/michelangelo/go/api"' not in text:
        text=text.replace('"testing"', '"testing"\n "github.com/michelangelo-ai/michelangelo/go/api"',1)
    text=text.replace('assert.IsType(t, &apiHandler{}, factoryHandler)', 'assert.Implements(t, (*api.Handler)(nil), factoryHandler)')
    path.write_text(text)


def prepare_controller_tests(source):
    """Controller unit fixtures represent authenticated internal service calls."""
    for name in ('deployment','triggerrun'):
        directory=Path(source)/'go/components'/name
        helper=directory/'qualification_context_test.go'
        for path in directory.glob('*_test.go'):
            if path==helper:continue
            text=path.read_text().replace('context.Background()', 'qualifiedControllerContext()')
            if 'context.' not in text:text=text.replace('\t"context"\n','')
            path.write_text(text)
        helper.write_text('''package '''+name+'''
import (
 "context"
 "github.com/michelangelo-ai/michelangelo/go/auth"
)
func qualifiedControllerContext() context.Context {
 return auth.WithPrincipal(context.Background(), auth.Principal{ID:"controller-test",Subject:"system:serviceaccount:ma-system:michelangelo",Enabled:true,Service:true,Role:"operator"})
}
''')
    path=Path(source)/'go/components/deployment/controller_test.go'
    replace(path,'WithObjects(deployment).', '''WithObjects(deployment,
 &v2pb.Model{ObjectMeta:metav1.ObjectMeta{Name:"test-model-v1",Namespace:"test-namespace",Labels:map[string]string{"michelangelo.ai/eligible":"true"},Annotations:map[string]string{"michelangelo.ai/publication-state":"eligible"}},Spec:v2pb.ModelSpec{SourcePipelineRun:&apipb.ResourceIdentifier{Name:"source",Namespace:"test-namespace"}}},
 &v2pb.PipelineRun{ObjectMeta:metav1.ObjectMeta{Name:"source",Namespace:"test-namespace"},Status:v2pb.PipelineRunStatus{State:v2pb.PIPELINE_RUN_STATE_SUCCEEDED}}).''',
 'michelangelo.ai/publication-state')


def prepare_runner_guards(source):
    target = Path(source)/'go/api/handler/handler.go'
    text = target.read_text()
    if 'handler.validateRunnerMutation(ctx, obj, nil)' in text:
        return
    for signature, guard in (
        ('func (handler *apiHandler) Create(', 'if err := handler.validateRunnerMutation(ctx, obj, nil); err != nil { return err }'),
        ('func (handler *apiHandler) UpdateStatus(', 'if err := denyRunner(ctx); err != nil { return err }'),
        ('func (handler *apiHandler) Delete(', 'if err := denyRunner(ctx); err != nil { return err }'),
        ('func (handler *apiHandler) List(', 'if err := denyRunner(ctx); err != nil { return err }'),
        ('func (handler *apiHandler) DeleteCollection(', 'if err := denyRunner(ctx); err != nil { return err }')):
        start = text.index(signature)
        body = text.index(' error {', start)+len(' error {')
        text = text[:body]+'\n    '+guard+text[body:]
    text = text.replace('auth.PreserveOwner(obj, original)', 'auth.PreserveOwner(obj, original)\n    if err := handler.validateRunnerMutation(ctx, obj, original); err != nil { return err }',1)
    start = text.index('func (handler *apiHandler) Get(')
    end = text.index('\n}\n',start)
    section = text[start:end]
    body = section.index(' error {')+len(' error {')
    section = section[:body]+'\n    if err := runnerRead(ctx, namespace, name, obj); err != nil { return err }'+section[body:]
    section = section.replace('return nil','return runnerResult(ctx, obj)')
    section = section.replace('return surfaceGrpcError(err, "get", namespace, name)', 'if err == nil { return runnerResult(ctx, obj) }; return surfaceGrpcError(err, "get", namespace, name)')
    text = text[:start]+section+text[end:]
    target.write_text(text)
