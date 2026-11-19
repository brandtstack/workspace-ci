# Fixture for family: custom + go. Its own two-stage Dockerfile, as an app repo would have: distroless, a binary
# with its own name (not /app), EXPOSE 80, PORT=80 and an exec-form HEALTHCHECK, which app.yml requires for custom + go.
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY server/ .
RUN go mod download
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/fixture ./cmd/fixture

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/fixture /fixture
ENV PORT=80
EXPOSE 80
USER 65532:65532
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["/fixture","healthcheck"]
ENTRYPOINT ["/fixture"]
