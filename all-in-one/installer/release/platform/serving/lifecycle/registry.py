"""Native Michelangelo model registration with required pilot lineage."""
from __future__ import annotations
import hashlib
import json
import os
import grpc
from google.protobuf.json_format import ParseDict


def api_client():
    from michelangelo.api.v2 import APIClient
    return APIClient(endpoint=os.environ['REGISTRY_ENDPOINT'], caller='oci-lifecycle-pilot')


def context():
    namespace = os.environ.get('MA_NAMESPACE') or os.environ.get('REGISTRY_NAMESPACE')
    run = os.environ.get('MA_PIPELINE_RUN_NAME')
    if not namespace or not run:
        raise RuntimeError('Native workflow namespace and source run are required')
    return namespace, run


def create_once(create, get, resource):
    try:
        return create(resource)
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.ALREADY_EXISTS:
            raise
        existing = get(resource.metadata.namespace, resource.metadata.name)
        if existing.spec != resource.spec:
            raise RuntimeError('Immutable registration conflicts with an existing resource') from exc
        return existing


def model_name(demo, package_checksum):
    namespace, run = context()
    digest = hashlib.sha256((namespace + run + package_checksum).encode()).hexdigest()[:16]
    return demo + '-' + digest


def register_candidate(demo, name, raw_uri, package_uri, package_checksum,
                       dataset_uri, dataset_checksum, evaluation_uri, metrics,
                       eligible, baseline_revision, framework, register_model=True,
                       training_provenance=None):
    from michelangelo.gen.api.v2 import model_pb2, model_family_pb2, evaluation_report_pb2
    namespace, run = context()
    client = api_client()
    source = {'name': run, 'namespace': namespace}
    report_name = name + '-evaluation'
    charts = [{'title': k, 'chartType': 'CHART_TYPE_SCALAR', 'scalar': {'scalarName': k, 'cellValue': {'number': float(v)}}}
              for k, v in metrics.items() if type(v) in (float, int)]
    charts.append({'title': 'Quality gate', 'chartType': 'CHART_TYPE_RICH_TEXT',
                   'richText': {'text': json.dumps({'eligible': eligible, 'baseline': baseline_revision, 'dataset_sha256': dataset_checksum, 'evaluation_uri': evaluation_uri})}})
    report = ParseDict({'metadata': {'name': report_name, 'namespace': namespace},
                        'spec': {'title': demo + ' lifecycle evaluation', 'sealed': True, 'sourcePipelineRun': source, 'charts': charts}}, evaluation_report_pb2.EvaluationReport())
    create_once(client.EvaluationReportService.create_evaluation_report,
                client.EvaluationReportService.get_evaluation_report, report)
    if not eligible or not register_model:
        return {'eligible': eligible, 'evaluation_report': report_name, 'model_registered': False}
    family = ParseDict({'metadata': {'name': demo, 'namespace': namespace},
                        'spec': {'name': demo, 'description': 'OCI internal operator pilot model family'}}, model_family_pb2.ModelFamily())
    create_once(client.ModelFamilyService.create_model_family, client.ModelFamilyService.get_model_family, family)
    annotations = {'michelangelo.ai/package-sha256': package_checksum,
                   'michelangelo.ai/dataset-sha256': dataset_checksum,
                   'michelangelo.ai/dataset-uri': dataset_uri,
                   'michelangelo.ai/evaluation-uri': evaluation_uri,
                   'michelangelo.ai/baseline-revision': baseline_revision,
                   'michelangelo.ai/demo': demo}
    annotations['michelangelo.ai/evaluation-dataset-uri'] = dataset_uri
    annotations['michelangelo.ai/evaluation-dataset-sha256'] = dataset_checksum
    if training_provenance:
        annotations['michelangelo.ai/training-provenance'] = json.dumps(training_provenance, sort_keys=True)
    else:
        annotations['michelangelo.ai/training-dataset-uri'] = dataset_uri
        annotations['michelangelo.ai/training-dataset-sha256'] = dataset_checksum
    model = ParseDict({'metadata': {'name': name, 'namespace': namespace, 'annotations': annotations,
                                   'labels': {'michelangelo/environment': 'nonprod', 'michelangelo.ai/eligible': 'true'}},
                       'spec': {'description': 'Eligible candidate; operator deployment required',
                                'trainingFramework': framework, 'sourcePipelineRun': source,
                                'performanceEvaluationReport': {'name': report_name, 'namespace': namespace},
                                'modelFamily': {'name': demo, 'namespace': namespace},
                                'modelArtifactUri': [raw_uri], 'deployableArtifactUri': [package_uri],
                                'packageType': 'DEPLOYABLE_MODEL_PACKAGE_TYPE_TRITON',
                                'kind': 'MODEL_KIND_REGRESSION' if 'housing' in demo else 'MODEL_KIND_BINARY_CLASSIFICATION'}}, model_pb2.Model())
    registered = create_once(client.ModelService.create_model, client.ModelService.get_model, model)
    return {'eligible': True, 'model_registered': True, 'model_revision': registered.metadata.name,
            'evaluation_report': report_name, 'project': namespace}


def resolve_revision(demo, revision):
    namespace, _ = context()
    model = api_client().ModelService.get_model(namespace, revision)
    if model.metadata.namespace != namespace or model.metadata.annotations.get('michelangelo.ai/demo') != demo:
        raise ValueError('Model revision is incompatible with this project/demo')
    if model.metadata.labels.get('michelangelo.ai/eligible') != 'true':
        raise ValueError('Model revision has not passed its quality gate')
    if not model.spec.deployable_artifact_uri:
        raise ValueError('Model has no serving package')
    return model
