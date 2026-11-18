# syntax=docker/dockerfile:1
# Shared SPA image (Vite etc.), built by .github/workflows/app.yml. Contract: dockerfiles/README.md.
# No lint, test or typecheck here: `ci / verify` gates the commit.
FROM node:24.22.3-alpine AS build
# The contract's `node:` must match this image's major version; see next.Dockerfile.
ARG NODE=24
RUN got="$(node -p "process.versions.node.split('.')[0]")" \
 && if [ "$got" != "$NODE" ]; then echo "contract node: $NODE but this image has Node $got" >&2; exit 1; fi
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
# BuildKit secret, never an ARG; optional for public-deps builds.
RUN --mount=type=secret,id=npm_token \
    if [ -f /run/secrets/npm_token ]; then NPM_TOKEN="$(cat /run/secrets/npm_token)"; export NPM_TOKEN; fi; \
    npm ci
COPY . .
RUN npm run build && mkdir -p nginx

FROM nginx:1.31.8-alpine
# Non-root: the main-context nginx.conf has no `user` directive and keeps pid and temp paths in /tmp.
COPY --from=ws nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=ws nginx/healthz.conf /etc/nginx/snippets/healthz.conf
COPY --from=build /app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/nginx/ /etc/nginx/snippets/
COPY --from=build /app/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /var/cache/nginx
USER nginx
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
