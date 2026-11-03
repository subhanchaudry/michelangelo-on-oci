"""Reviewed dependency corrections applied only to reconstructed source."""
from pathlib import Path
import hashlib
import json
import shutil
from identity_overlay import replace

ROOT = Path(__file__).parent
PYTHON = 'python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe'
STATIC = 'gcr.io/distroless/static-debian13@sha256:a8a6666cb7b13df7d18fd65b4264c2edafbce92acc0f32c423b5470d7d717665'
TRITON = 'nvcr.io/nvidia/tritonserver@sha256:1f08352ff9318894ca24bb36a532ad450b1289f7a6aeb163ae752a446c400fe6'
GO = 'golang@sha256:672c944cfd07599b5f1bb179869270cdc94c6254e41db4d431259693cb286b82'
COLLECTOR_COMMIT = 'c9ddfc2459dabc4149b8f383df2f6f6325e9b56c'


def prepare_collector(original, destination, commit):
    original, destination = Path(original), Path(destination)
    if commit != COLLECTOR_COMMIT:
        raise RuntimeError('Collector source commit differs from reviewed input')
    module = original/'historyserver/go.mod'
    if hashlib.sha256(module.read_bytes()).hexdigest() != 'c28a81c1ed1a7aa89617e2eb27168bcd02d8e22adaf44d3a67a8c2b87bf606d3':
        raise RuntimeError('Collector original module differs from reviewed input')
    if not destination.exists():
        shutil.copytree(original/'historyserver', destination)
    for name in ('go.mod', 'go.sum'):
        shutil.copyfile(ROOT/'dependencies/collector'/name, destination/name)
    dockerfile = destination/'Dockerfile.collector'
    replace(dockerfile, 'FROM golang:1.25.1 AS builder', 'FROM '+GO+' AS builder')
    replace(dockerfile, 'FROM ubuntu:22.04\nRUN apt-get update && apt-get upgrade -y && rm -rf /var/cache/apt/ && apt-get install -y ca-certificates', 'FROM '+STATIC)
    # The collector is statically linked by its upstream Makefile. Preserve
    # its default UID; the workload security context sets the runtime UID.
    return destination


def prepare_source(source):
    source = Path(source)
    module = source/'go/go.mod'
    if module.exists():
        reviewed = ROOT/'dependencies/platform'
        provenance = json.loads((reviewed/'provenance.json').read_text())
        expected = (reviewed/'go.mod').read_bytes()
        allowed = {provenance['upstream_patched_go_mod_sha256'], hashlib.sha256(expected).hexdigest()}
        if hashlib.sha256(module.read_bytes()).hexdigest() not in allowed:
            raise RuntimeError('Platform module differs from the reviewed dependency base')
        for name in ('go.mod','go.sum'):
            shutil.copyfile(reviewed/name, source/'go'/name)
    replace(source/'docker/service.Dockerfile',
        'FROM gcr.io/distroless/base-debian12:latest@sha256:fabbf1c0c357a3d42550111351daed089b20a2c954df13ee2fcff60602515e84',
        'FROM '+STATIC)
    replace(source/'docker/ui-prebuilt.Dockerfile',
        'RUN apk add --no-cache --upgrade libexpat',
        'RUN apk add --no-cache --upgrade libexpat=2.10.0-r0 pcre2=10.49-r0 libx11=1.8.14-r0 tiff=4.7.2-r0 brotli-libs=1.2.1-r0 curl=8.23.0-r0 libcurl=8.23.0-r0')


def prepare_demo(source):
    source = Path(source)
    for name in ('delivery', 'serving', 'cpu', 'builder', 'ray-vendor', 'certifi', 'virtualenv'):
        target = source/'docker'/(name+'-requirements.lock')
        shutil.copyfile(ROOT/'dependencies'/(name+'-requirements.lock'), target)
        target.chmod(0o644)
    shutil.copyfile(ROOT/'dependencies/patch-ray-vendor.py', source/'docker/patch-ray-vendor.py')
    dockerfile = source/'docker/support-runtime.Dockerfile'
    replace(dockerfile,
        'ARG PYTHON_BASE=python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef',
        'ARG PYTHON_BASE='+PYTHON)
    replace(dockerfile,
        'ARG TRITON_BASE=nvcr.io/nvidia/tritonserver@sha256:71a23d2f27001af58c242380fe05c9d46d5dbc79913d62c60de72543964267df',
        'ARG TRITON_BASE='+TRITON)
    replace(dockerfile,
        'FROM ${TRITON_BASE} AS triton',
        'COPY docker/ray-vendor-requirements.lock docker/patch-ray-vendor.py /opt/runtime-patches/\n'
        'RUN python3 /opt/runtime-patches/patch-ray-vendor.py /opt/michelangelo/python /opt/runtime-patches/ray-vendor-requirements.lock\n'
        'FROM ${TRITON_BASE} AS triton')
    # This pilot serves prebuilt Python models. Keep runtime libraries, but
    # remove development headers and the unused profiler, including the
    # unowned CUDA copy which the package manager does not remove.
    replace(dockerfile,
        'FROM ${TRITON_BASE} AS triton\nCOPY --from=serving-dependencies',
        'FROM ${TRITON_BASE} AS triton\n'
        'USER 0:0\n'
        'COPY docker/certifi-requirements.lock /opt/runtime-patches/certifi-requirements.lock\n'
        'COPY docker/virtualenv-requirements.lock /opt/runtime-patches/virtualenv-requirements.lock\n'
        'RUN python3 -m pip install --no-cache-dir --no-deps --require-hashes --upgrade -r /opt/runtime-patches/certifi-requirements.lock -r /opt/runtime-patches/virtualenv-requirements.lock\n'
        'RUN apt-get update && apt-get purge -y linux-libc-dev libssl-dev nsight-systems-cli-2026.5.1 && '
        'apt-get install -y --no-install-recommends libssl3t64=3.0.13-0ubuntu3.16 openssl=3.0.13-0ubuntu3.16 libbrotli1=1.1.0-2ubuntu0.1 && '
        'rm -rf /usr/local/cuda-13.4/NsightSystems-cli-2026.5.1 /var/lib/apt/lists/*\n'
        'COPY --from=serving-dependencies')
