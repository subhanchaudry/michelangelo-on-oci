# MySQL Router 26.10.0; preserve the vendor entrypoint and nonroot identity.
FROM container-registry.oracle.com/mysql/community-router@sha256:3de30bd493403d2cc87f0a1070686911ded03e473161c4c235bddfe03dfc0d3b AS runtime-security
USER 0:0
# Refresh Oracle Linux security packages without switching the pinned Router series.
RUN microdnf clean all && microdnf --disablerepo='mysql*' update -y && microdnf clean all
USER 999:999
