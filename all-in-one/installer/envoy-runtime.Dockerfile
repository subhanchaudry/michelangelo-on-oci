FROM docker.io/envoyproxy/envoy@sha256:49d3e8b6246d1a804acffaebe42f2799edc77b77a37a97709ef742af90b6d223 AS runtime-security
USER 0:0
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
USER 65534:65534
