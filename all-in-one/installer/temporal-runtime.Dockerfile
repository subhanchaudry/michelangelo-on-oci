# Source directories are verified against pinned commits before the build.
FROM --platform=$BUILDPLATFORM docker.io/library/golang@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0 AS build
ENV GOOS=linux GOARCH=amd64 CGO_ENABLED=0
WORKDIR /src/temporal
COPY temporal/ ./
RUN CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/temporal-server ./cmd/server && CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/temporal-sql-tool ./cmd/tools/sql
WORKDIR /src/dockerize
COPY dockerize/ ./
RUN CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/dockerize .
FROM docker.io/temporalio/server@sha256:16de74d893e813053b82551eb4463c795b368d2029520b4c8fe2e47e1c336bb9 AS legacy
FROM docker.io/temporalio/admin-tools@sha256:d6655e288364110b61204fbae5f103232b2c5c78e647a27bc11603d274145809 AS admin
USER 0:0
# This distribution needs the MySQL schema tool and namespace CLI only.
RUN rm -f /usr/local/bin/tdbg /usr/local/bin/temporal-cassandra-tool /usr/local/bin/temporal-elasticsearch-tool && rm -rf /etc/temporal/schema
COPY --from=build /out/temporal-sql-tool /usr/local/bin/temporal-sql-tool
COPY temporal/schema/mysql /etc/temporal/schema/mysql
# Git checkouts inherit the private installer umask; schema assets are public.
RUN chmod -R a+rX /etc/temporal/schema/mysql
COPY temporal/LICENSE /usr/share/licenses/temporal/LICENSE
USER 1000:1000
FROM admin AS server
USER 0:0
RUN apk add --no-cache bash
COPY --from=legacy /etc/temporal /etc/temporal
COPY --from=build /out/temporal-server /out/dockerize /usr/local/bin/
COPY dockerize/LICENSE /usr/share/licenses/dockerize/LICENSE
RUN chown -R 1000:1000 /etc/temporal
USER 1000:1000
WORKDIR /etc/temporal
ENTRYPOINT ["/etc/temporal/entrypoint.sh"]
CMD []
