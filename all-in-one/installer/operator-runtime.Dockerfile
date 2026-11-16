FROM --platform=$BUILDPLATFORM docker.io/library/golang@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0 AS build-base
ENV GOOS=linux GOARCH=amd64 CGO_ENABLED=0

FROM build-base AS kuberay-build
COPY kuberay /src/kuberay
WORKDIR /src/kuberay/ray-operator
RUN go build -mod=readonly -trimpath -o /out/manager . && go clean -cache -modcache

FROM build-base AS gateway-build
COPY gateway /src/gateway
WORKDIR /src/gateway
RUN go build -mod=readonly -trimpath -o /out/envoy-gateway ./cmd/envoy-gateway && go clean -cache -modcache

FROM build-base AS kyverno-build
COPY kyverno /src/kyverno
WORKDIR /src/kyverno
RUN go build -mod=readonly -trimpath -ldflags="-X github.com/kyverno/kyverno/pkg/version.BuildVersion=v1.19.2-oci.1" -o /out/ ./cmd/kyverno ./cmd/kyverno-init ./cmd/cli/kubectl-kyverno ./cmd/background-controller ./cmd/cleanup-controller ./cmd/reports-controller ./cmd/readiness-checker && go clean -cache -modcache

FROM gcr.io/distroless/static-debian13@sha256:a8a6666cb7b13df7d18fd65b4264c2edafbce92acc0f32c423b5470d7d717665 AS runtime
USER 65532:65532
ENTRYPOINT ["/app"]

FROM runtime AS kuberay
COPY --from=kuberay-build /out/manager /app
COPY --from=kuberay-build /src/kuberay/LICENSE /licenses/kuberay-LICENSE

FROM runtime AS gateway
COPY --from=gateway-build /out/envoy-gateway /app
COPY --from=gateway-build /src/gateway/LICENSE /licenses/gateway-LICENSE

FROM runtime AS kyverno
ARG BINARY
COPY --from=kyverno-build /out/${BINARY} /app
COPY --from=kyverno-build /src/kyverno/LICENSE /licenses/kyverno-LICENSE
COPY --from=kyverno-build /src/kyverno/cmd/kyverno/kodata/NOTICE /licenses/kyverno-NOTICE
