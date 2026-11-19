"""Explicit connection waits for the reviewed generated SDK service methods."""
import ast
from pathlib import Path

SERVICES = {'deployment':'Deployment','spark_job':'SparkJob','ray_job':'RayJob',
            'trigger_run':'TriggerRun','model':'Model','pipeline':'Pipeline',
            'pipeline_run':'PipelineRun','model_family':'ModelFamily',
            'evaluation_report':'EvaluationReport','ray_cluster':'RayCluster',
            'cached_output':'CachedOutput','project':'Project'}


def prepare_source(root):
    directory = Path(root)/'python/michelangelo/api/v2/services/gen'
    if {path.stem for path in directory.glob('*.py') if path.stem != '__init__'} != set(SERVICES):
        raise ValueError('Generated SDK service inventory differs from reviewed source')
    pending = {}
    count = 0
    for stem, kind in SERVICES.items():
        path = directory/(stem+'.py')
        text = path.read_text()
        calls = [n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Attribute)
                 and isinstance(n.func.value.value,ast.Name)
                 and n.func.value.value.id=='self' and n.func.value.attr=='_stub']
        expected = {action+kind for action in ('Create','Get','Update','Delete','List')} | {'Delete'+kind+'Collection'}
        if len(calls)!=6 or {n.func.attr for n in calls}!=expected:
            raise ValueError('Generated SDK service methods differ from reviewed source')
        replacements = []
        for call in calls:
            keywords = {kw.arg:kw.value for kw in call.keywords}
            if set(keywords) not in ({'metadata','timeout'},{'metadata','timeout','wait_for_ready'}):
                raise ValueError('Unreviewed SDK RPC call options')
            if not isinstance(keywords['timeout'],ast.Name) or keywords['timeout'].id!='timeout':
                raise ValueError('SDK caller deadline changed')
            if 'wait_for_ready' in keywords:
                value=keywords['wait_for_ready']
                if not isinstance(value,ast.Constant) or value.value is not True:
                    raise ValueError('Unexpected SDK wait override')
            else:
                old=ast.get_source_segment(text,call)
                if not old.endswith(')') or text.count(old)!=1:
                    raise ValueError('SDK call is not uniquely identified')
                replacements.append((old,old[:-1]+', wait_for_ready=True)'))
            count+=1
        for old,new in replacements:text=text.replace(old,new)
        ast.parse(text);pending[path]=text
    for path,text in pending.items():
        path.write_text(text);path.chmod(0o644)
    return count
