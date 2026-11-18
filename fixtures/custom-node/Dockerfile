# Fixture for family: custom + node. Its own Dockerfile, as an app repo would have: non-root, EXPOSE 80, PORT=80,
# an ENTRYPOINT wrapper. See dockerfiles/README.md "custom".
FROM node:24.22.3-alpine
# A custom Dockerfile declares this when the contract has build-args (this fixture has none).
ARG PUBLIC_BUILD_ENV_B64=""
WORKDIR /app
ENV NODE_ENV=production PORT=80
COPY package.json ./
COPY src ./src
# --chmod=0755: the smoke runs start.sh directly as the image user.
COPY --chmod=0755 start.sh entrypoint.sh ./
USER node
EXPOSE 80
ENTRYPOINT ["/app/entrypoint.sh"]
