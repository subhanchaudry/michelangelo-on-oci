# Client-only setup image: installer invokes mysql/mysqladmin via sh, never the server entrypoint.
FROM docker.io/library/mysql@sha256:72c368d898861f3b3a07af0e6d690400f44f048799ed46822783d9224cc2c86d
USER 0:0
RUN microdnf remove -y mysql-shell && rm -f /usr/local/bin/gosu /usr/local/bin/docker-entrypoint.sh && microdnf clean all
ENTRYPOINT []
USER 65534:65534
CMD ["mysql", "--version"]
