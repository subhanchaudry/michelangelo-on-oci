FROM --platform=$BUILDPLATFORM docker.io/library/golang@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0 AS build
ENV GOOS=linux GOARCH=amd64 CGO_ENABLED=0
COPY caddy /src/caddy
WORKDIR /src/caddy
RUN go build -mod=readonly -trimpath -o /out/caddy ./cmd/caddy
FROM docker.io/library/caddy@sha256:fc9cbc100585871d18d4e451cca7021785c90a7006087427ae5ceb3e4efe6424
COPY --from=build /out/caddy /usr/bin/caddy
COPY --from=build /src/caddy/LICENSE /licenses/caddy-LICENSE
