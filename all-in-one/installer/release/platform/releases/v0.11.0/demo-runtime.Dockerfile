# Application packaging around Uber's SDK. No mutation of generated workflow code.
# Supply a reviewed upstream examples image digest; never a previous pilot image.
ARG EXAMPLES_BASE
FROM ${EXAMPLES_BASE}
USER root
ENV POETRY_DYNAMIC_VERSIONING_BYPASS=0.11.0
COPY python /opt/michelangelo-sdk
RUN python3 -m pip install --no-cache-dir --no-deps /opt/michelangelo-sdk && \
    python3 -m pip install --no-cache-dir importlib_resources==6.5.2 zipp==3.23.0 && \
    rm -rf /opt/michelangelo-sdk /app/michelangelo
COPY demo-source/ /app/
ENV PYTHONPATH=/app:/app/oci_lifecycle
WORKDIR /app
USER 65534:65534
