# Fixture for skip-image-checks: root, not port 80, and exits at once. CI goes green only if those checks are skipped.
FROM node:24.22.3-alpine
ARG PUBLIC_BUILD_ENV_B64=""
EXPOSE 8090
CMD ["node", "-e", "process.exit(1)"]
