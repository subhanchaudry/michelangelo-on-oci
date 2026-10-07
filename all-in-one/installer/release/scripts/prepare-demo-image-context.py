#!/usr/bin/env python3
"""Copy an explicit, source-only demo application into an assembled release.

The task image contains its code, so upstream run_task works without rewriting
the Ray Starlark entrypoint or downloading an arbitrary source overlay at runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    source=args.source.resolve()
    out=source/'demo-source'
    out.mkdir(exist_ok=False)
    demos=out/'oci_demos'; demos.mkdir()
    (demos/'__init__.py').write_text('')
    for name in ('workflows.py','lifecycle_workflows.py'):
        shutil.copy2(ROOT/'examples/studio-demos'/name,demos/name)
    for name in ('personalization-ranking/train_ranking.py','personalization-ranking/ranking_mlops.py','personalization-ranking/ranking_advanced_mlops.py','movielens-collaborative-filtering/movielens_cf_mlops.py','fraud-anomaly-detection/fraud_anomaly_mlops.py','california-housing-regression/california_housing_mlops.py'):
        shutil.copy2(ROOT/'examples'/name,demos/Path(name).name)
    lifecycle=out/'oci_lifecycle'; lifecycle.mkdir()
    for path in (ROOT/'platform/serving/lifecycle').glob('*.py'):
        shutil.copy2(path,lifecycle/path.name)
    # Original training pipelines retain their existing images and source packages.
    # This image is for the 32 lifecycle workflows, including bounded retraining.
    shutil.copy2(ROOT/'platform/releases/v0.11.0/demo-runtime.Dockerfile',source/'docker/demo-runtime.Dockerfile')
    for name in ('support-runtime.Dockerfile','serving-requirements.lock','delivery-requirements.lock'):
        shutil.copy2(ROOT/'platform/releases/v0.11.0'/name,source/'docker'/name)
    manifest={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    (source/'demo-source-checksums.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Prepared',len(manifest),'source files; no credentials or runtime state copied.')

if __name__=='__main__':main()
