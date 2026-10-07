# Run from a clean assembled source after prepare-demo-image-context.py.
# Copy this Dockerfile and the two lockfiles into its docker/ directory.
ARG PYTHON_BASE=python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef
ARG TRITON_BASE=nvcr.io/nvidia/tritonserver@sha256:71a23d2f27001af58c242380fe05c9d46d5dbc79913d62c60de72543964267df
FROM ${PYTHON_BASE} AS serving-dependencies
COPY docker/serving-requirements.lock /requirements.lock
RUN python3 -m pip install --no-cache-dir --require-hashes --no-deps --target /opt/michelangelo/python -r /requirements.lock
FROM ${TRITON_BASE} AS triton
COPY --from=serving-dependencies /opt/michelangelo/python /opt/michelangelo/python
COPY demo-source/oci_lifecycle/ /opt/michelangelo/
COPY demo-source/oci_demos/ /opt/michelangelo/oci_demos/
ENV PYTHONPATH=/opt/michelangelo/python:/opt/michelangelo HF_HOME=/tmp/huggingface TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=2
USER 65532:65532
FROM ${PYTHON_BASE} AS delivery
COPY docker/delivery-requirements.lock /requirements.lock
RUN python3 -m pip install --no-cache-dir --require-hashes --no-deps --target /opt/michelangelo/python -r /requirements.lock
COPY demo-source/oci_lifecycle/package.py demo-source/oci_lifecycle/delivery.py demo-source/oci_lifecycle/log_server.py /opt/michelangelo/
ENV PYTHONPATH=/opt/michelangelo/python:/opt/michelangelo
USER 65532:65532
CMD ["python3", "/opt/michelangelo/delivery.py"]
