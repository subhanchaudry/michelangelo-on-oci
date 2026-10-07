"""Create deterministic CPU Triton packages from actual trained artifacts."""
import io
import json
from pathlib import Path
import tarfile


def build_package(demo, revision, model_directory):
    directory=Path(model_directory)
    root=Path(__file__).resolve().parent
    dimensions='[-1, 2]' if demo=='bert-cola-finetuning' else '[-1]'
    config=('name: '+json.dumps(revision)+'\nbackend: "python"\nmax_batch_size: 0\n'
            'input [{name: "RECORDS" data_type: TYPE_STRING dims: [1]}]\n'
            'output [{name: "SCORES" data_type: TYPE_FP64 dims: '+dimensions+'}]\n'
            'instance_group [{kind: KIND_CPU count: 1}]\n')
    files={'config.pbtxt':config.encode(),'1/model.py':(root/'triton_model.py').read_bytes(),
           '1/metadata.json':json.dumps({'demo':demo,'contract_version':1},sort_keys=True).encode()}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink(): raise ValueError('Model artifacts cannot contain symlinks')
        if path.is_file():
            name='1/'+path.relative_to(directory).as_posix()
            if name in files: raise ValueError('Model artifact would overwrite its adapter contract')
            files[name]=path.read_bytes()
    if demo in ('california-housing-xgb','bert-cola-finetuning'):
        required='1/model.ubj' if demo=='california-housing-xgb' else '1/model/config.json'
        if required not in files: raise ValueError('Incomplete trained model artifact')
    elif '1/model.json' not in files: raise ValueError('Missing trained JSON model')
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as archive:
        for name,data in sorted(files.items()):
            member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o600;member.mtime=0
            archive.addfile(member,io.BytesIO(data))
    return output.getvalue()
