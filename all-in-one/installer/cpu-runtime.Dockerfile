# CPU/Ray qualification runtime. The SDK's separate Spark plugin remains
# available, but is not selected by this eight-model execution image.
ARG PYTHON_BASE=python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe
FROM ${PYTHON_BASE} AS sdk-build
ENV POETRY_DYNAMIC_VERSIONING_BYPASS=0.13.0 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY docker/builder-requirements.lock /builder-requirements.lock
RUN python3 -m pip install --no-cache-dir --require-hashes --no-deps -r /builder-requirements.lock
COPY python /sdk
RUN python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /sdk

FROM ${PYTHON_BASE} AS runtime-security
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
COPY docker/cpu-requirements.lock /requirements.lock
RUN python3 -m pip install --no-cache-dir --require-hashes --no-deps -r /requirements.lock
COPY docker/ray-vendor-requirements.lock docker/patch-ray-vendor.py /opt/runtime-patches/
RUN python3 /opt/runtime-patches/patch-ray-vendor.py /usr/local/lib/python3.12/site-packages /opt/runtime-patches/ray-vendor-requirements.lock
COPY --from=sdk-build /wheels /wheels
RUN python3 -m pip install --no-cache-dir --no-deps '/wheels/'*.whl && \
    python3 -m pip check && \
    python3 -c 'import importlib.util; assert importlib.util.find_spec("pyspark") is None' && \
    python3 -m pip freeze > /opt/michelangelo-runtime.lock && \
    rm -rf /wheels
# KubeRay supplies wget health probes to every Ray container.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/*
COPY demo-source/ /app/
ENV PYTHONPATH=/app:/app/oci_lifecycle OMP_NUM_THREADS=2 AWS_EC2_METADATA_DISABLED=true
WORKDIR /app
USER 65534:65534
CMD ["python3"]
