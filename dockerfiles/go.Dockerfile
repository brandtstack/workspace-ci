# Shared Go image, built by .github/workflows/app.yml. Contract: dockerfiles/README.md.
# GO is major.minor from the contract (e.g. 1.27); GO_MAIN the main package; GO_DIR the module dir.
# No vet or tests here: `ci / verify` gates the commit.
ARG GO=1.27
FROM golang:${GO}-alpine AS build
ARG GO_MAIN=.
ARG GO_DIR=.
WORKDIR /src
COPY . .
WORKDIR /src/${GO_DIR}
RUN go mod download
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/app ${GO_MAIN}
# Empty data dir for every go image: a named volume mounted at /data takes this ownership on first mount.
RUN mkdir -p /out/data

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/app /app
COPY --from=build --chown=65532:65532 /out/data /data
ENV PORT=80
EXPOSE 80
USER 65532:65532
# Exec form: distroless has no shell. The app implements `healthcheck` (dockerfiles/README.md "go").
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["/app","healthcheck"]
ENTRYPOINT ["/app"]
